"""FastAPI application serving Scope's web UI and authenticated API."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout

from .answers import answer
from .contracts import Answer, AnswerInput, ChangeAccess, DemoSignIn, DocumentSpec, Principal, PublicDocument, SearchResult, Slug
from .fixtures import DEMO_ACTORS, demo_token
from .settings import Settings
from .store import Forbidden, NotFound, Store, open_pool

WEB = Path(__file__).with_name("web")
BEARER = HTTPBearer(auto_error=False)


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    pool = store.pool if store else open_pool(settings.database_url)
    owns_pool = store is None
    store = store or Store(pool)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if owns_pool:
            pool.open(wait=True, timeout=8)
        try:
            yield
        finally:
            if owns_pool:
                pool.close()

    app = FastAPI(title="Scope Search", description="Permission-aware workplace search.", lifespan=lifespan)

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/v1/") or request.url.path == "/":
            response.headers["Cache-Control"] = "private, no-store"
        if request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'self'; "
                "base-uri 'none'; form-action 'self'"
            )
        return response

    async def unavailable(_request, _error):
        return JSONResponse(status_code=503, content={"detail": "Search storage is unavailable."})

    app.add_exception_handler(DatabaseError, unavailable)
    app.add_exception_handler(PoolTimeout, unavailable)

    @app.exception_handler(Forbidden)
    async def forbidden(_request, _error):
        return JSONResponse(status_code=403, content={"detail": "Workspace administrator access is required."})

    @app.exception_handler(NotFound)
    async def missing(_request, error):
        return JSONResponse(status_code=404, content={"detail": str(error)})

    def principal(credentials: HTTPAuthorizationCredentials | None = Depends(BEARER)) -> Principal:
        token = credentials.credentials if credentials else ""
        if 24 <= len(token) <= 160 and (settings.demo_enabled or not token.startswith("demo-scope-")):
            found = store.authenticate(token)
            if found is not None:
                return found
        raise HTTPException(401, "A valid workspace token is required.", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(WEB / "index.html", media_type="text/html")

    @app.get("/readyz")
    def ready() -> dict:
        store.ready()
        return {"status": "ready"}

    if settings.demo_enabled:
        @app.get("/v1/demo/actors")
        def demo_actors() -> dict:
            return {"actors": DEMO_ACTORS}

        @app.post("/v1/demo/session")
        def demo_session(request: DemoSignIn) -> dict:
            if request.actor not in {entry["actor"] for entry in DEMO_ACTORS}:
                raise HTTPException(404, "Demo account was not found.")
            token = demo_token(request.actor)
            if store.authenticate(token) is None:
                raise HTTPException(404, "Initialize the demo workspace first.")
            return {"token": token}

    @app.get("/v1/me", response_model=Principal)
    def me(user: Principal = Depends(principal)):
        return user

    @app.get("/v1/search", response_model=SearchResult)
    def search(q: Annotated[str, Query(min_length=1, max_length=160)], user: Principal = Depends(principal)):
        cleaned = q.strip()
        if not cleaned:
            raise HTTPException(422, "Enter a search query.")
        read = store.search(user, cleaned)
        return SearchResult(query=cleaned, cache="hit" if read.hit else "miss", results=read.results)

    @app.get("/v1/documents/{slug}", response_model=PublicDocument)
    def document(slug: Slug, user: Principal = Depends(principal)):
        result = store.document(user, slug)
        if result is None:
            raise HTTPException(404, "Document was not found.")
        return result

    @app.post("/v1/answer", response_model=Answer)
    def respond(request: AnswerInput, user: Principal = Depends(principal)):
        return answer(store, settings, user, request.query)

    @app.get("/v1/admin/access")
    def access(user: Principal = Depends(principal)) -> dict:
        return store.access(user)

    @app.put("/v1/admin/groups/{group}/members/{handle}")
    def change_access(group: Slug, handle: Slug, request: ChangeAccess, user: Principal = Depends(principal)) -> dict:
        return store.membership(user, group, handle, request.enabled)

    @app.put("/v1/admin/documents")
    def save_document(request: DocumentSpec, user: Principal = Depends(principal)) -> dict:
        return store.save_document(user, request)

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    return app
