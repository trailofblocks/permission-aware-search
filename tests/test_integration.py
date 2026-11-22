import os
import unittest
from dataclasses import replace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import connect, sql
from psycopg.conninfo import make_conninfo

from scope_search.api import create_app
from scope_search.contracts import TenantSpec
from scope_search.fixtures import demo_token, seed_demo
from scope_search.settings import Settings
from scope_search.store import Store, migrate, open_pool, set_scope

DATABASE = os.getenv("SCOPE_TEST_DATABASE_URL")
QUERY = "vendor policy"
PRIVATE = "vendor-finance-memo"
CHANGE = "/v1/admin/groups/finance/members/maya"


def credentials(actor):
    return {"Authorization": f"Bearer {demo_token(actor)}"}


@unittest.skipUnless(DATABASE, "Set SCOPE_TEST_DATABASE_URL to run PostgreSQL integration tests.")
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema_name = f"scope_test_{uuid4().hex[:10]}"
        schema = sql.Identifier(schema_name)
        ddl = connect(DATABASE, autocommit=True)
        cls.addClassCleanup(ddl.close)
        ddl.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        cls.addClassCleanup(ddl.execute, sql.SQL("DROP SCHEMA {} CASCADE").format(schema))
        cls.settings = Settings(database_url=make_conninfo(DATABASE, options=f"-csearch_path={schema_name}"), demo_enabled=True)
        cls.pool = cls.enterClassContext(open_pool(cls.settings.database_url))
        cls.pool.wait(8)
        migrate(cls.pool)
        cls.store = Store(cls.pool)
        seed_demo(cls.store)
        cls.client = cls.enterClassContext(TestClient(create_app(cls.settings, cls.store)))

    def setUp(self):
        self.client.put(CHANGE, headers=credentials("elena"), json={"enabled": True}).raise_for_status()
        self.addCleanup(self.restore)

    def restore(self):
        self.client.put(CHANGE, headers=credentials("elena"), json={"enabled": True}).raise_for_status()

    def search(self, actor):
        return self.client.get("/v1/search", headers=credentials(actor), params={"q": QUERY}).raise_for_status().json()

    def test_warm_cache_revocation_and_document_visibility(self):
        self.search("maya")
        warm = self.search("maya")
        self.assertEqual(warm["cache"], "hit")
        finance = next(hit for hit in warm["results"] if hit["slug"] == PRIVATE)
        self.assertIn("suppliers paid within ten business days.", finance["excerpt"])
        self.client.put(CHANGE, headers=credentials("elena"), json={"enabled": False}).raise_for_status()
        # A repeat initialization preserves existing permissions.
        seed_demo(self.store)
        after = self.search("maya")
        self.assertEqual(after["cache"], "miss")
        self.assertNotIn(PRIVATE, {hit["slug"] for hit in after["results"]})
        open_revoked = self.client.get(f"/v1/documents/{PRIVATE}", headers=credentials("maya"))
        open_missing = self.client.get("/v1/documents/missing", headers=credentials("maya"))
        self.assertEqual(open_revoked.status_code, 404)
        self.assertEqual(open_revoked.json(), open_missing.json())
        self.assertEqual(open_revoked.headers["Cache-Control"], "private, no-store")

    def test_database_policy_filters_by_tenant_and_group(self):
        support = self.store.authenticate(demo_token("theo"))
        with self.pool.connection() as connection:
            connection.execute("SET LOCAL ROLE scope_document_reader")
            self.assertEqual(connection.execute("SELECT count(*) AS total FROM documents").fetchone()["total"], 0)
            connection.execute("SELECT set_config('scope.tenant', 'northstar', true)")
            self.assertEqual(connection.execute("SELECT count(*) AS total FROM documents").fetchone()["total"], 0)
        with self.pool.connection() as connection:
            set_scope(connection, support)
            rows = connection.execute("SELECT tenant_id, slug FROM documents").fetchall()
            self.assertEqual({row["tenant_id"] for row in rows}, {"northstar"})
            self.assertNotIn(PRIVATE, {row["slug"] for row in rows})
        harbor = self.search("ari")
        self.assertEqual([item["title"] for item in harbor["results"]], ["Harbor vendor policy"])
        self.assertNotIn(PRIVATE, {hit["slug"] for hit in self.search("elena")["results"]})

    def test_cached_ids_are_reauthorized_on_every_read(self):
        support = self.store.authenticate(demo_token("theo"))
        result = self.store.search(support, QUERY)
        key = (support.tenant_id, support.handle, result.revision, QUERY, 10)
        self.store.cache.save(key, (PRIVATE, "vendor-policy"))
        try:
            read = self.store.search(support, QUERY)
            self.assertTrue(read.hit)
            self.assertEqual([item.slug for item in read.results], ["vendor-policy"])
        finally:
            self.store.cache.save(key, tuple(item.slug for item in result.results))

    def test_model_answer_is_rechecked_if_permission_changes(self):
        def select(_settings, _query, hits):
            self.assertIn(PRIVATE, {hit.slug for hit in hits})
            admin = self.store.authenticate(demo_token("elena"))
            self.store.membership(admin, "finance", "maya", False)
            return [PRIVATE]

        config = replace(self.settings, ollama_url="http://127.0.0.1:11434")
        with TestClient(create_app(config, self.store)) as app, patch("scope_search.answers.model_selection", select):
            result = app.post("/v1/answer", headers=credentials("maya"), json={"query": QUERY}).raise_for_status().json()
        self.assertEqual(result["mode"], "extractive")
        self.assertNotIn(PRIVATE, {hit["slug"] for hit in result["citations"]})

    def test_only_admin_can_change_members_and_documents(self):
        result = self.client.put(CHANGE, headers=credentials("theo"), json={"enabled": False})
        self.assertEqual(result.status_code, 403)
        missing = self.client.put(CHANGE, headers=credentials("ari"), json={"enabled": False})
        self.assertEqual(missing.status_code, 404)
        payload = {"slug":"admin-draft", "title":"Quarterly project plan", "collection":"Plans",
                   "group_slug":"finance", "body":"Quarterly project plan for Finance."}
        self.assertEqual(self.client.put("/v1/admin/documents", headers=credentials("theo"), json=payload).status_code, 403)
        self.client.put("/v1/admin/documents", headers=credentials("elena"), json=payload).raise_for_status()
        self.assertEqual(self.client.get("/v1/documents/admin-draft", headers=credentials("theo")).status_code, 404)
        self.assertEqual(self.client.get("/v1/documents/admin-draft", headers=credentials("maya")).status_code, 200)

    def test_demo_credentials_are_disabled_in_normal_mode(self):
        workspace = TenantSpec(tenant_id="private", name="Private", users=[
            {"handle":"owner", "name":"Owner", "role":"admin"},
        ])
        tokens = self.store.import_workspace(workspace)
        with TestClient(create_app(replace(self.settings, demo_enabled=False), self.store)) as app:
            self.assertEqual(app.get("/v1/demo/actors").status_code, 404)
            self.assertEqual(app.get("/v1/me", headers=credentials("maya")).status_code, 401)
            owner = app.get("/v1/me", headers={"Authorization":"Bearer " + tokens["owner"]}).raise_for_status().json()
            self.assertEqual(owner["tenant_id"], "private")
