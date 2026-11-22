"""Query-time document authorization, revisioned caching, and workspace mutations."""

from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from secrets import token_urlsafe
from threading import Lock
from time import monotonic

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .contracts import DocumentSpec, Hit, Principal, PublicDocument, TenantSpec

DOC_FIELDS = "slug, title, collection, group_slug, body, updated_at"
HIT_FIELDS = "slug, title, collection, group_slug, updated_at"
SNIPPET = """
    coalesce((
        SELECT sentence
        FROM regexp_split_to_table(body, '(?<=[.!?])[[:space:]]+') WITH ORDINALITY AS passages(sentence, position)
        WHERE length(sentence) <= 360 AND to_tsvector('english', sentence) @@ q
        ORDER BY position LIMIT 1
    ), regexp_replace(ts_headline('english', body, q, 'MaxWords=36, MinWords=20'), '</?b>', '', 'g')) AS excerpt
"""
READER = "scope_document_reader"


class NotFound(Exception):
    pass


class DuplicateWorkspace(Exception):
    pass


class Forbidden(Exception):
    pass


def digest(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def open_pool(url: str) -> ConnectionPool:
    return ConnectionPool(url, open=False, min_size=1, max_size=8, timeout=5, kwargs={
        "row_factory": dict_row, "connect_timeout": 4,
    })


def migrate(pool: ConnectionPool) -> None:
    schema = files("scope_search").joinpath("schema.sql").read_text(encoding="utf-8")
    with pool.connection() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('scope-schema-v1', 0))")
        reader = connection.execute("SELECT * FROM pg_roles WHERE rolname=%s", (READER,)).fetchone()
        if reader is None:
            connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT NOBYPASSRLS").format(sql.Identifier(READER)))
        elif reader["rolsuper"] or reader["rolbypassrls"] or reader["rolcanlogin"]:
            raise RuntimeError("The document reader must be an unprivileged NOLOGIN role.")
        connection.execute(sql.SQL("GRANT {} TO CURRENT_USER").format(sql.Identifier(READER)))
        namespace = connection.execute("SELECT current_schema() AS name").fetchone()["name"]
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(namespace), sql.Identifier(READER)))
        connection.execute(schema)


@dataclass(frozen=True)
class SearchRead:
    revision: int
    hit: bool
    results: list[Hit]


class ResultIds:
    def __init__(self, capacity: int = 256, ttl: float = 120):
        self.capacity = capacity
        self.ttl = ttl
        self.entries = OrderedDict()
        self.lock = Lock()

    def get(self, key: tuple) -> tuple[str, ...] | None:
        with self.lock:
            cached = self.entries.pop(key, None)
            if cached is None or cached[0] < monotonic():
                return None
            self.entries[key] = cached
            return cached[1]

    def save(self, key: tuple, slugs: tuple[str, ...]) -> None:
        with self.lock:
            self.entries[key] = (monotonic() + self.ttl, slugs)
            self.entries.move_to_end(key)
            if len(self.entries) > self.capacity:
                self.entries.popitem(last=False)


def require_admin(user: Principal) -> None:
    if user.role != "admin":
        raise Forbidden("Workspace administrator access is required.")


def set_scope(connection, user: Principal) -> None:
    connection.execute("""
        SELECT set_config('scope.tenant', %s, true), set_config('scope.subject', %s, true),
               set_config('statement_timeout', '5000', true)
    """,
                       (user.tenant_id, user.handle))
    connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(READER)))


def persist_document(connection, tenant_id: str, document: DocumentSpec) -> None:
    connection.execute("""
        INSERT INTO documents (tenant_id, slug, title, collection, group_slug, body)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, slug) DO UPDATE SET
          title=excluded.title, collection=excluded.collection, group_slug=excluded.group_slug,
          body=excluded.body, updated_at=clock_timestamp()
    """, (tenant_id, document.slug, document.title, document.collection, document.group_slug, document.body))


class Store:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool
        self.cache = ResultIds()

    def ready(self) -> None:
        with self.pool.connection() as connection:
            connection.execute("SELECT tenant_id FROM tenants LIMIT 1")

    def authenticate(self, token: str) -> Principal | None:
        with self.pool.connection() as connection:
            row = connection.execute("""
                SELECT u.tenant_id, t.name AS tenant_name, u.handle, u.name, u.role
                FROM scope_users u JOIN tenants t USING (tenant_id) WHERE u.token_hash=%s
            """, (digest(token),)).fetchone()
        return None if row is None else Principal.model_validate(row)

    def import_workspace(self, workspace: TenantSpec, tokens: dict | None = None,
                         skip_existing: bool = False) -> dict:
        tokens = tokens if tokens is not None else {user.handle: token_urlsafe(32) for user in workspace.users}
        with self.pool.connection() as connection:
            added = connection.execute("""
                INSERT INTO tenants (tenant_id, name) VALUES (%s, %s)
                ON CONFLICT DO NOTHING RETURNING tenant_id
            """, (workspace.tenant_id, workspace.name)).fetchone()
            if added is None:
                if skip_existing:
                    return {}
                raise DuplicateWorkspace("Workspace already exists. Import creates new workspaces.")
            for group in workspace.groups:
                connection.execute("INSERT INTO scope_groups VALUES (%s, %s, %s)",
                                   (workspace.tenant_id, group.slug, group.name))
            for user in workspace.users:
                connection.execute("INSERT INTO scope_users VALUES (%s, %s, %s, %s, %s)",
                                   (workspace.tenant_id, user.handle, user.name, user.role, digest(tokens[user.handle])))
                for group in user.groups:
                    connection.execute("INSERT INTO group_memberships VALUES (%s, %s, %s)",
                                       (workspace.tenant_id, group, user.handle))
            for document in workspace.documents:
                persist_document(connection, workspace.tenant_id, document)
        return tokens

    def search(self, user: Principal, query: str, limit: int = 10) -> SearchRead:
        with self.pool.connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            revision = connection.execute("SELECT revision FROM tenants WHERE tenant_id=%s",
                                          (user.tenant_id,)).fetchone()["revision"]
            set_scope(connection, user)
            key = (user.tenant_id, user.handle, revision, query, limit)
            cached = self.cache.get(key)
            criteria = """
                CROSS JOIN websearch_to_tsquery('english', %s) AS q
                WHERE tenant_id=%s AND querytree(q) NOT IN ('', 'T') AND search_vector @@ q
            """
            if cached is None:
                rows = connection.execute(f"""
                    SELECT {HIT_FIELDS}, {SNIPPET} FROM documents {criteria}
                    ORDER BY ts_rank_cd(search_vector, q) DESC, slug LIMIT %s
                """, (query, user.tenant_id, limit)).fetchall()
                self.cache.save(key, tuple(row["slug"] for row in rows))
            elif cached:
                rows = connection.execute(f"""
                    SELECT {HIT_FIELDS}, {SNIPPET} FROM documents {criteria} AND slug=ANY(%s)
                    ORDER BY array_position(%s::text[], slug)
                """, (query, user.tenant_id, list(cached), list(cached))).fetchall()
            else:
                rows = []
        return SearchRead(revision, cached is not None, [Hit.model_validate(row) for row in rows])

    def document(self, user: Principal, slug: str) -> PublicDocument | None:
        with self.pool.connection() as connection:
            set_scope(connection, user)
            row = connection.execute(f"SELECT {DOC_FIELDS} FROM documents WHERE tenant_id=%s AND slug=%s",
                                     (user.tenant_id, slug)).fetchone()
        return None if row is None else PublicDocument.model_validate(row)

    def access(self, user: Principal) -> dict:
        require_admin(user)
        with self.pool.connection() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            groups = connection.execute("SELECT slug, name FROM scope_groups WHERE tenant_id=%s ORDER BY name",
                                        (user.tenant_id,)).fetchall()
            users = connection.execute("""
                SELECT u.handle, u.name, u.role, ARRAY(
                    SELECT group_slug FROM group_memberships
                    WHERE tenant_id=u.tenant_id AND member_handle=u.handle ORDER BY group_slug
                ) AS groups
                FROM scope_users u WHERE u.tenant_id=%s ORDER BY u.name
            """, (user.tenant_id,)).fetchall()
            audit = connection.execute("""
                SELECT actor_handle, action, target, revision, occurred_at
                FROM access_audit WHERE tenant_id=%s ORDER BY id DESC LIMIT 12
            """, (user.tenant_id,)).fetchall()
        return {"users": users, "groups": groups, "audit": audit}

    @contextmanager
    def change(self, user: Principal):
        require_admin(user)
        with self.pool.connection() as connection:
            connection.execute("SELECT revision FROM tenants WHERE tenant_id=%s FOR UPDATE", (user.tenant_id,))
            yield connection

    def record_change(self, connection, user: Principal, action: str, target: str) -> int:
        revision = connection.execute("UPDATE tenants SET revision=revision+1 WHERE tenant_id=%s RETURNING revision",
                                      (user.tenant_id,)).fetchone()["revision"]
        connection.execute("""
            INSERT INTO access_audit (tenant_id, actor_handle, action, target, revision) VALUES (%s, %s, %s, %s, %s)
        """, (user.tenant_id, user.handle, action, target, revision))
        return revision

    def membership(self, user: Principal, group: str, handle: str, enabled: bool) -> dict:
        with self.change(user) as connection:
            scope = connection.execute("""
                SELECT g.slug FROM scope_groups g JOIN scope_users u USING(tenant_id)
                WHERE g.tenant_id=%s AND g.slug=%s AND u.handle=%s
            """, (user.tenant_id, group, handle)).fetchone()
            if scope is None:
                raise NotFound("Group or member was not found.")
            if enabled:
                change = connection.execute("""
                    INSERT INTO group_memberships VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """, (user.tenant_id, group, handle))
            else:
                change = connection.execute("""
                    DELETE FROM group_memberships WHERE tenant_id=%s AND group_slug=%s AND member_handle=%s
                """, (user.tenant_id, group, handle))
            if change.rowcount:
                self.record_change(connection, user, "grant" if enabled else "revoke", f"{group}/{handle}")
            return {"group": group, "member": handle, "enabled": enabled, "changed": bool(change.rowcount)}

    def save_document(self, user: Principal, document: DocumentSpec) -> dict:
        with self.change(user) as connection:
            if document.group_slug and connection.execute("""
                SELECT 1 FROM scope_groups WHERE tenant_id=%s AND slug=%s
            """, (user.tenant_id, document.group_slug)).fetchone() is None:
                raise NotFound("Document group was not found.")
            persist_document(connection, user.tenant_id, document)
            revision = self.record_change(connection, user, "save_document", document.slug)
            return {"slug": document.slug, "revision": revision}
