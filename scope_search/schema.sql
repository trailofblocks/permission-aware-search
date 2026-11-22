CREATE TABLE IF NOT EXISTS tenants (
    tenant_id text PRIMARY KEY,
    name text NOT NULL,
    revision bigint NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS scope_users (
    tenant_id text NOT NULL REFERENCES tenants(tenant_id),
    handle text NOT NULL,
    name text NOT NULL,
    role text NOT NULL CHECK (role IN ('member', 'admin')),
    token_hash text NOT NULL UNIQUE,
    PRIMARY KEY (tenant_id, handle)
);

CREATE TABLE IF NOT EXISTS scope_groups (
    tenant_id text NOT NULL REFERENCES tenants(tenant_id),
    slug text NOT NULL,
    name text NOT NULL,
    PRIMARY KEY (tenant_id, slug)
);

CREATE TABLE IF NOT EXISTS group_memberships (
    tenant_id text NOT NULL,
    group_slug text NOT NULL,
    member_handle text NOT NULL,
    FOREIGN KEY (tenant_id, group_slug) REFERENCES scope_groups(tenant_id, slug),
    FOREIGN KEY (tenant_id, member_handle) REFERENCES scope_users(tenant_id, handle),
    PRIMARY KEY (tenant_id, group_slug, member_handle)
);

CREATE TABLE IF NOT EXISTS documents (
    tenant_id text NOT NULL REFERENCES tenants(tenant_id),
    slug text NOT NULL,
    title text NOT NULL,
    collection text NOT NULL,
    group_slug text,
    body text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    search_vector tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A') ||
        setweight(to_tsvector('english', body), 'B')
    ) STORED,
    FOREIGN KEY (tenant_id, group_slug) REFERENCES scope_groups(tenant_id, slug),
    PRIMARY KEY (tenant_id, slug)
);
CREATE INDEX IF NOT EXISTS documents_search_vector ON documents USING GIN(search_vector);

CREATE TABLE IF NOT EXISTS access_audit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenants(tenant_id),
    actor_handle text NOT NULL,
    action text NOT NULL,
    target text NOT NULL,
    revision bigint NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS access_audit_tenant ON access_audit(tenant_id, id DESC);

ALTER TABLE group_memberships ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scoped_memberships ON group_memberships;
CREATE POLICY scoped_memberships ON group_memberships FOR SELECT TO scope_document_reader
    USING (tenant_id = current_setting('scope.tenant', true)
        AND member_handle = current_setting('scope.subject', true));

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS scoped_documents ON documents;
CREATE POLICY scoped_documents ON documents FOR SELECT TO scope_document_reader
    USING (tenant_id = current_setting('scope.tenant', true)
        AND nullif(current_setting('scope.subject', true), '') IS NOT NULL AND (
        group_slug IS NULL OR EXISTS (
            SELECT 1
            FROM group_memberships m
            WHERE m.tenant_id = documents.tenant_id
              AND m.group_slug = documents.group_slug
              AND m.member_handle = current_setting('scope.subject', true)
        )
    ));

GRANT SELECT ON documents, group_memberships TO scope_document_reader;
