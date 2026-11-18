"""Entry point.

    python -m backend.main                      -> web server (127.0.0.1, random port)
    python -m backend.main --run <task.json>    -> run ONE automation task (internal)

The same entry point is used by the PyInstaller executable (``backend.exe --run ...``).
Heavy imports are deferred so a runner subprocess does not load the web stack.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import threading
import time


def _emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


def serve(args: argparse.Namespace) -> int:
    from pathlib import Path

    from backend.core.config import AppConfig, Paths
    from backend.core.logging_setup import setup_logging

    paths = (Paths(Path(args.data_dir)) if args.data_dir else AppConfig.default_paths()).ensure()
    setup_logging(paths.logs)

    token = os.environ.pop("PW_TOKEN", None)          # removed so runners never inherit it
    if not token:
        if not args.dev:
            _emit({"event": "fatal", "code": "no_token", "message": "PW_TOKEN is not set."})
            return 2
        token = secrets.token_urlsafe(32)

    # Fail early (with a clean message for Electron) if the database is from a newer version.
    from backend.database.migrations import SchemaMismatchError, SchemaTooNewError, ensure_schema
    from backend.database.session import Database
    db = Database(paths.db_file)
    try:
        ensure_schema(db.engine, paths.db_file, paths.backups)
    except SchemaTooNewError as exc:
        _emit({"event": "fatal", "code": "schema_too_new", "message": str(exc)})
        return 3
    except SchemaMismatchError as exc:
        _emit({"event": "fatal", "code": "schema_mismatch", "message": str(exc)})
        return 4
    finally:
        db.dispose()

    import uvicorn

    from backend.app import create_app

    cfg = AppConfig(paths=paths, token=token, parent_pid=args.parent_pid)
    app = create_app(cfg, announce=True, dev_token_in_ready_line=args.dev)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", args.port))                # 127.0.0.1 only; port 0 = random free port
    app.state.port = sock.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))

    if args.parent_pid:
        from backend.core.process_tree import parent_gone

        def watch() -> None:
            while not server.should_exit:
                time.sleep(2)
                if parent_gone(args.parent_pid):
                    server.should_exit = True
                    time.sleep(15)
                    os._exit(0)
        threading.Thread(target=watch, name="parent-watchdog", daemon=True).start()

    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:          # Ctrl+C: uvicorn already shut down cleanly; just exit quietly
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--run" in argv:                                # runner mode: no web imports
        i = argv.index("--run")
        from backend.task_manager.runner import run_main
        return run_main(argv[i + 1])

    p = argparse.ArgumentParser(prog="purelywireless-backend")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--data-dir", default=None)
    p.add_argument("--parent-pid", type=int, default=None)
    p.add_argument("--dev", action="store_true", help="generate a token if PW_TOKEN is unset and print it")
    return serve(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
