from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automations.registry import AutomationSpec, build_default_registry  # noqa: E402
from backend.app import create_app  # noqa: E402
from backend.core.config import AppConfig, Paths  # noqa: E402

TOKEN = "test-token-123"
H = {"X-PW-Token": TOKEN}


def make_registry(**overrides):
    """Default registry plus demo variants with short timeouts for the timeout tests."""
    from dataclasses import replace
    from automations.specs.demo import DEMO_SPEC

    def extra(reg):
        reg.register(replace(DEMO_SPEC, id="demo_short", name="Demo (short timeout)", timeout_s=2, idle_timeout_s=None, **overrides))
        reg.register(replace(DEMO_SPEC, id="demo_idle", name="Demo (idle timeout)", timeout_s=None, idle_timeout_s=2))
        reg.register(replace(DEMO_SPEC, id="demo_locked", name="Demo (locked visible)", headless_default=False, headless_locked=True))
    return build_default_registry(extra)


@pytest.fixture()
def paths(tmp_path) -> Paths:
    return Paths(tmp_path / "PurelyWireless")


@pytest.fixture()
def client(paths):
    cfg = AppConfig(paths=paths, token=TOKEN)
    app = create_app(cfg, registry=make_registry())
    with TestClient(app) as c:
        c.headers.update(H)
        c.app_state = app.state
        yield c


def wait_for(client, task_id, statuses, timeout=40.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = client.get(f"/api/tasks/{task_id}").json()
        if last["status"] in statuses:
            return last
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} did not reach {statuses}; last={last}")


TERMINAL = {"Completed", "Failed", "Cancelled", "Interrupted"}


def submit(client, params=None, markets=("Houston",), automation="demo", allow_duplicate=False):
    r = client.post("/api/tasks", json={"automation_id": automation, "params": params or {},
                                        "markets": list(markets), "allow_duplicate": allow_duplicate})
    return r
