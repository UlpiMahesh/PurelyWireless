"""Fake demo automation used by tests and for developing the UI.

It deliberately mimics the awkward things the legacy scripts do:
  * prints (with emoji) at import time,
  * attaches a logging.StreamHandler at import time,
  * writes logs through ``logging`` (stderr) and ``print`` (stdout),
  * can spawn a child process (stand-in for a Playwright browser).
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from typing import Any

from openpyxl import Workbook

from automations.adapters.base import AdapterContext
from automations.specs.demo import DemoParams

# --- import-time behaviour copied from the legacy scripts (on purpose) -------
print("DEMO MODULE IMPORTED ✅ (import-time print)")
_legacy_logger = logging.getLogger("demo_legacy")
_legacy_logger.setLevel(logging.INFO)
_legacy_logger.handlers.clear()
_legacy_logger.addHandler(logging.StreamHandler())  # binds sys.stderr at import time
# -----------------------------------------------------------------------------


def run(ctx: AdapterContext) -> dict[str, Any]:
    p = DemoParams(**ctx.params)
    ctx.stage("Starting")
    print(f"[demo] headless={ctx.headless} markets={ctx.markets} ✅")
    _legacy_logger.warning("2026-01-01 00:00:00 | WARNING | legacy-style logging line")

    child = None
    if p.spawn_child or p.leak_child:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        ctx.log(f"spawned child pid={child.pid}")

    if p.write_diagnostics:
        (ctx.scratch_dir / "screenshots").mkdir(exist_ok=True)
        (ctx.scratch_dir / "screenshots" / "login_rejected.png").write_bytes(b"png")
        (ctx.scratch_dir / "logs").mkdir(exist_ok=True)
        (ctx.scratch_dir / "logs" / "legacy.log").write_text("legacy log", encoding="utf-8")
        (ctx.scratch_dir / "fail_1Z999_attempt1.png").write_bytes(b"png")
    if p.hard_exit:
        import os
        os._exit(7)

    ctx.stage("Working")
    total = p.steps
    for step in range(1, total + 1):
        if p.fail_at_step == step:
            raise RuntimeError(f"Demo failure at step {step}")
        time.sleep(p.delay_ms / 1000)
        print(f"[demo] step {step}/{total}")
        ctx.progress(step, total)

    if child is not None and not p.leak_child:
        child.kill()

    ctx.stage("Saving")
    wb = Workbook()
    ws = wb.active
    ws.append(["Market", "Value"])
    for i, m in enumerate(ctx.markets or ["(none)"]):
        ws.append([m, i])
    out = ctx.scratch_dir / "output" / "demo_result.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    ctx.file(out, name="Demo", markets=ctx.markets)

    return {
        "failures": [{"market": m, "message": "Demo failure", "kind": "error"} for m in p.failures],
        "stats": {"steps": total},
    }
