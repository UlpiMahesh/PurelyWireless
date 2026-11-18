from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from automations import paths as auto_paths
from backend.app import create_app
from backend.core.config import AppConfig, Paths
from backend.database import migrations
from backend.database.session import Database
from backend.task_manager.protocol import StreamCapture, classify_level
from tests.conftest import H, TOKEN, make_registry


# ------------------------------------------------------------------ paths / env
def test_data_dir_override_and_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_DATA_DIR", str(tmp_path / "data"))
    assert auto_paths.default_data_dir() == tmp_path / "data"
    p = Paths(tmp_path / "data").ensure()
    for d in (p.database, p.files, p.input, p.logs, p.backups, p.temp, p.browser_data, p.config):
        assert d.is_dir()
    assert p.db_file.parent == p.database
    assert p.credentials_file == p.config / "marketlogins.xlsx"


def test_default_data_dir_is_localappdata_on_windows(monkeypatch):
    monkeypatch.delenv("PW_DATA_DIR", raising=False)
    if os.name == "nt":
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
        assert str(auto_paths.default_data_dir()).endswith(r"AppData\Local\PurelyWireless")
    else:
        assert auto_paths.default_data_dir().name == "PurelyWireless"


def test_headless_default_uses_legacy_value_unless_env_set(monkeypatch):
    monkeypatch.delenv("PW_HEADLESS", raising=False)
    assert auto_paths.headless_default(True) is True
    assert auto_paths.headless_default(False) is False
    monkeypatch.setenv("PW_HEADLESS", "0")
    assert auto_paths.headless_default(True) is False
    monkeypatch.setenv("PW_HEADLESS", "1")
    assert auto_paths.headless_default(False) is True


# ------------------------------------------------------------------ stream capture
def test_classify_level():
    assert classify_level("2026-01-01 | WARNING | x") == "warning"
    assert classify_level("2026-01-01 | ERROR | x") == "error"
    assert classify_level("[Dallas] ❌ LOGIN FAILED") == "error"
    assert classify_level("[Dallas] ⚠️ something") == "warning"
    assert classify_level("[Dallas] ✅ ok") == "info"
    assert classify_level("native noise", "debug") == "debug"


def test_stream_capture_splits_lines_and_drains():
    got = []
    cap = StreamCapture(got.append)
    cap.write("a\nb")
    cap.write("c\r\n\n  \nlast")
    cap.drain()
    assert got == ["a", "bc", "last"]
    with pytest.raises(OSError):      # no fileno(): Playwright then keeps its own stderr handling
        cap.fileno()


# ------------------------------------------------------------------ security
def test_every_http_route_needs_the_token(paths):
    app = create_app(AppConfig(paths=paths, token=TOKEN), registry=make_registry())
    with TestClient(app) as c:
        for url in ("/api/health", "/api/tasks", "/api/files", "/api/automations", "/api/settings", "/api/stats", "/api/logs"):
            assert c.get(url).status_code == 401, url
            assert c.get(url, headers={"X-PW-Token": "wrong"}).status_code == 401, url
            assert c.get(url, headers=H).status_code == 200, url
        assert c.get("/api/health", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
        assert c.post("/api/tasks", json={"automation_id": "demo"}).status_code == 401
        assert c.get("/docs").status_code == 404        # no unauthenticated docs


def test_websocket_rejects_bad_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=nope"):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass


def test_health(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["schema_version"] == migrations.CURRENT_VERSION
    assert h["running_task_id"] is None and h["queued"] == 0


# ------------------------------------------------------------------ database
def test_fresh_database_records_schema_version(tmp_path):
    db = Database(tmp_path / "x.db")
    assert migrations.ensure_schema(db.engine, tmp_path / "x.db", tmp_path / "bk") == migrations.CURRENT_VERSION
    assert migrations.ensure_schema(db.engine, tmp_path / "x.db", tmp_path / "bk") == migrations.CURRENT_VERSION
    with db.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"


def test_database_from_newer_version_is_refused(tmp_path, monkeypatch):
    db = Database(tmp_path / "x.db")
    migrations.ensure_schema(db.engine, tmp_path / "x.db", tmp_path / "bk")
    monkeypatch.setattr(migrations, "CURRENT_VERSION", 0)
    with pytest.raises(migrations.SchemaTooNewError):
        migrations.ensure_schema(db.engine, tmp_path / "x.db", tmp_path / "bk")


def test_migration_runs_after_backup(tmp_path, monkeypatch):
    db = Database(tmp_path / "x.db")
    migrations.ensure_schema(db.engine, tmp_path / "x.db", tmp_path / "bk")
    ran = []
    monkeypatch.setattr(migrations, "CURRENT_VERSION", 2)
    monkeypatch.setitem(migrations.MIGRATIONS, 2, lambda engine: ran.append(2))
    assert migrations.ensure_schema(db.engine, tmp_path / "x.db", tmp_path / "bk") == 2
    assert ran == [2]
    assert list((tmp_path / "bk").glob("pre-migration_v1_*.db"))
    assert migrations._current(db.engine) == 2


# ------------------------------------------------------------------ registry + validation
def test_automations_listed_with_schema(client):
    items = {a["id"]: a for a in client.get("/api/automations").json()}
    assert "demo" in items and items["demo"]["requires_markets"] is True
    assert "steps" in items["demo"]["params_schema"]["properties"]
    assert client.get("/api/automations/nope").status_code == 404


def test_validation_errors(client):
    r = client.post("/api/tasks", json={"automation_id": "nope", "markets": ["A"]})
    assert r.status_code == 404 and r.json()["error"]["code"] == "unknown_automation"
    r = client.post("/api/tasks", json={"automation_id": "demo", "markets": []})
    assert r.status_code == 422 and r.json()["error"]["code"] == "markets_required"
    r = client.post("/api/tasks", json={"automation_id": "demo", "markets": ["A"], "params": {"steps": 0}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_params"
    assert client.get("/api/tasks/9999").status_code == 404


# ------------------------------------------------------------------ settings
def test_settings_defaults_and_headless_rules(client):
    s = client.get("/api/settings").json()
    assert s["theme"] == "system" and s["headless.demo"] is True and s["headless.demo_locked"] is False
    assert client.put("/api/settings/headless.demo", json={"value": False}).json()["headless.demo"] is False
    assert client.put("/api/settings/theme", json={"value": "dark"}).json()["theme"] == "dark"
    assert client.put("/api/settings/theme", json={"value": "pink"}).status_code == 422
    assert client.put("/api/settings/headless.demo_locked", json={"value": True}).status_code == 422
    assert client.put("/api/settings/headless.nope", json={"value": True}).status_code == 404
    assert client.put("/api/settings/whatever", json={"value": 1}).status_code == 422


def test_server_side_never_imports_adapters_or_legacy_code():
    """Adapters/legacy modules have import-time side effects; only the runner may import them."""
    import subprocess
    code = ("import sys; sys.path.insert(0, %r)\n"
            "import backend.app, automations.registry as r; r.build_default_registry()\n"
            "bad = [m for m in sys.modules if m.startswith(('automations.adapters.demo', 'automations.legacy.'))]\n"
            "print('BAD', bad)\n" % str(Path(__file__).resolve().parents[1]))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout
    assert out.strip() == "BAD []", out
