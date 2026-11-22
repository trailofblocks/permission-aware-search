"""Run Scope Search and administer PostgreSQL workspaces."""

import argparse
import json
import sys
from pathlib import Path

import httpx
import uvicorn
from psycopg import Error as DatabaseError
from psycopg_pool import PoolTimeout

from .contracts import TenantSpec
from .fixtures import seed_demo
from .settings import Settings
from .store import DuplicateWorkspace, Store, migrate, open_pool
from .verify import verify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scope-search", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Apply the PostgreSQL schema.")
    init.add_argument("--demo", action="store_true", help="Create two synthetic workspaces.")
    load = commands.add_parser("import", help="Create a workspace and return its users' one-time access tokens.")
    load.add_argument("manifest", type=Path)
    serve = commands.add_parser("serve", help="Serve the web UI and API.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8894)
    demo = commands.add_parser("verify", help="Test the seeded search and revocation scenario.")
    demo.add_argument("--url", default="http://127.0.0.1:8894")
    demo.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    try:
        if args.command == "serve":
            uvicorn.run("scope_search.api:create_app", factory=True, host=args.host, port=args.port, access_log=False)
        elif args.command == "verify":
            result = json.dumps(verify(args.url), indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(result + "\n", encoding="utf-8")
            print(result)
        else:
            with open_pool(settings.database_url) as pool:
                pool.wait(timeout=8)
                if args.command == "init":
                    migrate(pool)
                    if args.demo:
                        seed_demo(Store(pool))
                    print("Scope database is ready.")
                else:
                    spec = TenantSpec.model_validate_json(args.manifest.read_bytes())
                    tokens = Store(pool).import_workspace(spec)
                    print(json.dumps({"workspace": spec.tenant_id, "bearer_tokens": tokens}, indent=2))
    except (OSError, ValueError, RuntimeError, DuplicateWorkspace, DatabaseError, PoolTimeout, httpx.HTTPError) as error:
        print(f"Scope Search: {error}", file=sys.stderr)
        return 1
    return 0
