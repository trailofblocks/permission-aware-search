# Scope Search

Scope is a permission-aware search app for internal policies, playbooks, and handbooks. It uses FastAPI and PostgreSQL full-text search and includes a browser UI for search, cited excerpts, and permission changes. The local demo contains two synthetic workspaces.

## Run the demo

```sh
docker compose up -d --build --wait
```

Open [Scope Search](http://127.0.0.1:8894/) and search for `vendor policy`. The demo opens as Maya Chen in Northstar Finance.

1. Repeat the query to see **Cached search**. Open the finance exceptions memo or its citation.
2. Switch to Theo Brooks and repeat the query. The shared policy remains visible. The finance memo is absent.
3. Switch to Elena Park, open **Access review**, and revoke Maya's Finance access.
4. Switch back to Maya and run the same query. The memo disappears from results, citations, and direct document access. No index rebuild is needed.
5. Switch to Ari Morgan. The same query returns Harbor's own vendor policy.

To run the scenario automatically and restore the membership it found at the start:

```sh
docker compose run --rm --no-deps app verify --url http://app:8894
```

The API documentation is at [`/docs`](http://127.0.0.1:8894/docs). Run `docker compose down` to stop the demo while retaining the database volume.

## How authorization works

- Every request resolves a bearer token to a workspace and user. Workspace and member IDs for document reads come from that authentication result.
- Searches, snippets, and direct document reads execute under a PostgreSQL role that cannot bypass row-level security. The policy allows workspace-wide documents or documents shared with a group the caller currently belongs to.
- The in-memory cache stores ranked document IDs. Its key includes the workspace, user, query, and workspace revision. Cached IDs are reauthorized in PostgreSQL before titles or excerpts are returned. A permission or document change updates the revision and writes an audit entry in the same transaction.
- Answers quote allowed passages and link to their documents. When a local model is configured, it selects citation IDs from authorized excerpts. The server builds the response from those excerpts and rechecks permissions after model selection. Requests use a database snapshot, so a response already handed to a caller remains visible in their browser until they refresh it.
- Demo account switching is available only when `SCOPE_DEMO=1`. Normal mode accepts randomly generated workspace tokens. The UI holds tokens in memory and document responses use `Cache-Control: private, no-store`.

## Use your own workspace

Install Python 3.12 and the pinned dependencies, then point `DATABASE_URL` at a PostgreSQL database. The initialization login needs permission to create or use the `scope_document_reader` role and own the application tables. The read policy is enforced when the application assumes that role.

```sh
python -m pip install -r requirements.txt
python -m scope_search init
python -m scope_search import examples/workspace.json
python -m scope_search serve
```

The import prints each user's bearer token once. Save them privately. The database stores token hashes. Open the UI and sign in with a token, or use it in the API. Use a new workspace slug for each import. For further changes, the admin API can update documents and group memberships.

| Route | Access |
| --- | --- |
| `GET /v1/me` | Signed-in identity and workspace |
| `GET /v1/search?q=vendor%20policy` | Authorized keyword search |
| `GET /v1/documents/{slug}` | Authorized full document |
| `POST /v1/answer` | Cited excerpts for a query |
| `GET /v1/admin/access` | Workspace groups, members, and recent permission changes |
| `PUT /v1/admin/groups/{group}/members/{handle}` | Set membership with `{"enabled": true}` or `false` |
| `PUT /v1/admin/documents` | Create or update a document from a workspace administrator |

Set `SCOPE_OLLAMA_URL` to an approved Ollama endpoint to enable model-selected citations. The default model tag is `qwen2.5:1.5b`; override it with `SCOPE_OLLAMA_MODEL`. If the model is unavailable, the answer uses the ranked excerpts. Hosted use needs your organization's TLS and identity provisioning. The demo stack binds to localhost.

## Verify

```sh
python -m unittest discover -s tests -v
```

Set `SCOPE_TEST_DATABASE_URL` to run the PostgreSQL tests in a disposable schema. The suite checks database isolation, warm-cache revocation, reauthorization of cached IDs, and revocation during model selection. GitHub Actions also starts the containerized demo and uploads its revocation report.

The local replay verified eight checks on real PostgreSQL. It showed three Finance results before revocation and two afterward. The restricted memo disappeared from both search and citations, and direct access returned HTTP 404. The optional local Qwen path was verified separately with a Finance-only question.
