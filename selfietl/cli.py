from __future__ import annotations

import argparse

import uvicorn

from selfietl.config import load_config
from selfietl.db import Database


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="selfietl")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Start FastAPI and serve the built React app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8765, type=int)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--data-dir", default=None)

    init_db = sub.add_parser("init-db", help="Initialize the local SQLite catalog")
    init_db.add_argument("--data-dir", default=None)

    args = parser.parse_args(argv)
    if args.command in (None, "serve"):
        if getattr(args, "data_dir", None):
            import os

            os.environ["SELFIE_TL_HOME"] = args.data_dir
        uvicorn.run("selfietl.server:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
    elif args.command == "init-db":
        config = load_config(args.data_dir)
        Database(config.db_path)
        print(f"Initialized {config.db_path}")
