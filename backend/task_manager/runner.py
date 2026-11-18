"""Runs ONE automation task inside its own process (``--run task.json``).

Why a subprocess: live logs, a real cancel (kill the tree incl. Playwright
browsers), isolation from the legacy scripts' import-time side effects, and no
asyncio/thread clashes with the web server.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path


def load_callable(dotted: str):
    module_name, _, func = dotted.partition(":")
    return getattr(importlib.import_module(module_name), func or "run")


def _parent_watchdog(parent_pid: int) -> None:
    """If the backend dies, take this whole runner tree down with it."""
    from backend.core.process_tree import kill_tree, parent_gone

    def loop() -> None:
        while True:
            time.sleep(2.0)
            if parent_gone(parent_pid):
                kill_tree(os.getpid())
                os._exit(1)

    threading.Thread(target=loop, name="parent-watchdog", daemon=True).start()


def run_main(task_file: str) -> int:
    from backend.task_manager.protocol import ProtocolWriter, StreamCapture, classify_level

    raw_out = sys.__stdout__
    if raw_out is None:  # pragma: no cover - windowless build misuse
        return 70
    proto = ProtocolWriter(raw_out)

    # Install capture BEFORE importing any automation code: legacy modules create
    # logging.StreamHandler() at import time and would bind the original stderr.
    out_cap = StreamCapture(lambda line: proto.log(line, classify_level(line, "info")))
    err_cap = StreamCapture(lambda line: proto.log(line, classify_level(line, "info")))
    sys.stdout, sys.stderr = out_cap, err_cap

    spec = json.loads(Path(task_file).read_text(encoding="utf-8"))
    try:
        os.chdir(spec["scratch_dir"])
    except OSError:
        pass
    if spec.get("parent_pid"):
        _parent_watchdog(int(spec["parent_pid"]))

    proto.send({"type": "hello", "pid": os.getpid()})
    code = 0
    try:
        from automations.adapters.base import AdapterContext

        fn = load_callable(spec["adapter"])
        ctx = AdapterContext(
            task_id=spec["task_id"],
            automation_id=spec["automation_id"],
            params=spec.get("params") or {},
            markets=spec.get("markets") or [],
            scratch_dir=Path(spec["scratch_dir"]),
            data_dir=Path(spec["data_dir"]),
            credentials_file=Path(spec["credentials_file"]),
            headless=bool(spec.get("headless", True)),
            _emit=lambda e: proto.send(e),
        )
        summary = fn(ctx) or {}
        out_cap.drain(); err_cap.drain()
        proto.send({"type": "done", "summary": summary})
    except SystemExit as exc:
        out_cap.drain(); err_cap.drain()
        if exc.code in (0, None):
            proto.send({"type": "done", "summary": {}})
        else:
            proto.send({"type": "error", "message": f"Automation exited with status {exc.code}", "traceback": ""})
            code = 1
    except BaseException as exc:  # noqa: BLE001 - everything must be reported
        out_cap.drain(); err_cap.drain()
        proto.send({
            "type": "error",
            "message": f"{type(exc).__name__}: {exc}"[:2000],
            "traceback": traceback.format_exc()[-8000:],
        })
        code = 1
    finally:
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    return code
