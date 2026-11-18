from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import create_app
from backend.core.config import AppConfig
from backend.database.migrations import ensure_schema
from backend.database.session import Database
from backend.models.tables import GeneratedFile, MarketLock, Task, TaskLog
from tests.conftest import H, TERMINAL, TOKEN, make_registry, submit, wait_for


def dead(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def wait_dead(pid: int, timeout=10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if dead(pid):
            return True
        time.sleep(0.1)
    return dead(pid)


def logs(client, tid):
    return client.get(f"/api/tasks/{tid}/logs").json()


def child_pid(client, tid) -> int:
    for _ in range(100):
        for line in logs(client, tid):
            if "spawned child pid=" in line["message"]:
                return int(line["message"].rsplit("=", 1)[1])
        time.sleep(0.1)
    raise AssertionError("child pid never logged")


# ------------------------------------------------------------------ happy path
def test_task_completes_with_logs_progress_and_file(client, paths):
    r = submit(client, {"steps": 5, "delay_ms": 50, "failures": ["Houston"]}, markets=["Houston", "Tulsa"])
    assert r.status_code == 201
    tid = r.json()["task"]["id"]
    t = wait_for(client, tid, TERMINAL)
    assert t["status"] == "Completed", t
    assert t["progress"] == 1.0 and (t["progress_done"], t["progress_total"]) == (5, 5)
    assert t["markets"] == ["Houston", "Tulsa"] and t["headless"] is True
    assert t["summary"]["failures"][0]["market"] == "Houston"
    assert t["started_at"].endswith("Z") and t["duration_s"] is not None

    msgs = [l["message"] for l in logs(client, tid)]
    joined = "\n".join(msgs)
    assert "DEMO MODULE IMPORTED ✅" in joined                 # print() at import time was captured
    assert "legacy-style logging line" in joined               # logging.StreamHandler created at import was captured
    assert "[demo] step 5/5" in joined and "Stage: Saving" in joined
    levels = {l["message"]: l["level"] for l in logs(client, tid)}
    assert levels[next(m for m in msgs if "legacy-style" in m)] == "warning"

    files = client.get("/api/files").json()
    assert files["total"] == 1
    f = files["items"][0]
    assert f["task_id"] == tid and f["automation_id"] == "demo" and f["markets"] == ["Houston", "Tulsa"]
    assert f["exists"] is True and f["size"] > 0 and f["ext"] == "xlsx"
    p = Path(f["path"])
    assert p.is_file() and p.is_relative_to(paths.files / "Demo")
    parts = p.relative_to(paths.files / "Demo").parts            # <Year>/<MM-Month>/<YYYY-MM-DD>/<file>
    assert len(parts) == 4 and parts[0].isdigit() and "-" in parts[1] and len(parts[2]) == 10
    assert f"_T{tid}" in p.name
    assert not (paths.task_scratch / str(tid)).exists()          # scratch cleaned
    assert (paths.task_logs / f"task_{tid}.log").is_file()       # raw per-task log kept


def test_file_filters(client):
    a = submit(client, {"steps": 1, "delay_ms": 0}, markets=["Houston"]).json()["task"]["id"]
    b = submit(client, {"steps": 1, "delay_ms": 0}, markets=["Tulsa", "CT"]).json()["task"]["id"]
    wait_for(client, a, TERMINAL), wait_for(client, b, TERMINAL)
    assert client.get("/api/files").json()["total"] == 2
    assert client.get("/api/files?market=tulsa").json()["total"] == 1       # tag filter, case-insensitive
    assert client.get("/api/files?market=CT").json()["items"][0]["task_id"] == b
    assert client.get("/api/files?market=Nope").json()["total"] == 0
    assert client.get("/api/files?automation_id=demo&ext=xlsx").json()["total"] == 2
    assert client.get("/api/files?q=demo").json()["total"] == 2
    assert client.get(f"/api/files?task_id={a}").json()["total"] == 1
    assert client.get("/api/files?sort=name&order=asc").status_code == 200
    fid = client.get("/api/files").json()["items"][0]["id"]
    assert client.get(f"/api/files/{fid}").json()["id"] == fid
    assert client.get("/api/files/999").status_code == 404


def test_failure_is_reported_with_traceback(client):
    tid = submit(client, {"steps": 3, "delay_ms": 0, "fail_at_step": 2}).json()["task"]["id"]
    t = wait_for(client, tid, TERMINAL)
    assert t["status"] == "Failed" and "Demo failure at step 2" in t["error"]
    assert any("Traceback" in l["message"] and l["level"] == "error" for l in logs(client, tid))
    assert client.get("/api/files").json()["total"] == 0


def test_runner_crash_is_failed_not_stuck(client):
    tid = submit(client, {"hard_exit": True}).json()["task"]["id"]
    t = wait_for(client, tid, TERMINAL)
    assert t["status"] == "Failed" and "exit code 7" in t["error"]


def test_diagnostics_are_preserved_and_scratch_removed(client, paths):
    tid = submit(client, {"steps": 1, "delay_ms": 0, "write_diagnostics": True}).json()["task"]["id"]
    wait_for(client, tid, TERMINAL)
    art = paths.task_logs / f"task_{tid}_artifacts"
    assert (art / "screenshots" / "login_rejected.png").is_file()
    assert (art / "logs" / "legacy.log").is_file()
    assert (art / "fail_1Z999_attempt1.png").is_file()
    assert not (art / "output").exists()
    assert not (paths.task_scratch / str(tid)).exists()


# ------------------------------------------------------------------ duplicates / queue
def test_duplicate_tasks_are_rejected_unless_allowed(client):
    first = submit(client, {"steps": 40, "delay_ms": 100, "note": "dup"}, markets=["Houston", "Tulsa"])
    tid = first.json()["task"]["id"]
    dup = submit(client, {"steps": 40, "delay_ms": 100, "note": "dup"}, markets=["tulsa", "HOUSTON"])  # same set, other order/case
    assert dup.status_code == 409
    err = dup.json()["error"]
    assert err["code"] == "duplicate_task" and err["existing_task_id"] == tid
    other = submit(client, {"steps": 40, "delay_ms": 100, "note": "dup"}, markets=["Houston"])
    assert other.status_code == 201                                   # different markets => not a duplicate
    forced = submit(client, {"steps": 40, "delay_ms": 100, "note": "dup"}, markets=["Houston", "Tulsa"], allow_duplicate=True)
    assert forced.status_code == 201
    for t in (tid, other.json()["task"]["id"], forced.json()["task"]["id"]):
        client.post(f"/api/tasks/{t}/cancel")
    wait_for(client, tid, TERMINAL)
    # once finished, the same task may be queued again
    wait_for(client, forced.json()["task"]["id"], TERMINAL)
    wait_for(client, other.json()["task"]["id"], TERMINAL)
    assert submit(client, {"steps": 40, "delay_ms": 100, "note": "dup"}, markets=["Houston", "Tulsa"]).status_code == 201


def test_fifo_and_only_one_task_runs_at_a_time(client):
    ids = [submit(client, {"steps": 2, "delay_ms": 300, "note": str(i)}, markets=[f"M{i}"]).json()["task"]["id"] for i in range(3)]
    max_active, deadline = 0, time.time() + 60
    while time.time() < deadline:
        sts = [client.get(f"/api/tasks/{i}").json()["status"] for i in ids]
        max_active = max(max_active, sum(s in ("Starting", "Running") for s in sts))
        if all(s == "Completed" for s in sts):
            break
        time.sleep(0.05)
    assert all(s == "Completed" for s in sts), sts
    assert max_active == 1
    started = [client.get(f"/api/tasks/{i}").json()["started_at"] for i in ids]
    assert started == sorted(started)                                 # FIFO


def test_market_lock_held_only_while_running(client):
    tid = submit(client, {"steps": 30, "delay_ms": 100}, markets=["Houston", "Tulsa"]).json()["task"]["id"]
    wait_for(client, tid, {"Running"})
    db: Database = client.app_state.db
    with db.session() as s:
        assert {l.market for l in s.scalars(select(MarketLock))} == {"houston", "tulsa"}
    client.post(f"/api/tasks/{tid}/cancel")
    wait_for(client, tid, TERMINAL)
    with db.session() as s:
        assert s.scalars(select(MarketLock)).all() == []


# ------------------------------------------------------------------ cancel
def test_cancel_running_task_kills_runner_and_its_children(client, paths):
    tid = submit(client, {"steps": 200, "delay_ms": 100, "spawn_child": True}).json()["task"]["id"]
    t = wait_for(client, tid, {"Running"})
    runner_pid, grandchild = t["runner_pid"], child_pid(client, tid)
    assert not dead(runner_pid) and not dead(grandchild)
    r = client.post(f"/api/tasks/{tid}/cancel")
    assert r.status_code == 200 and r.json()["status"] in ("Cancelling", "Cancelled")
    t = wait_for(client, tid, {"Cancelled"})
    assert wait_dead(runner_pid) and wait_dead(grandchild)
    assert t["finished_at"] and t["runner_pid"] is None
    assert not (paths.task_scratch / str(tid)).exists()
    assert client.post(f"/api/tasks/{tid}/cancel").status_code == 409   # already finished


def test_cancel_queued_task_never_starts(client):
    a = submit(client, {"steps": 100, "delay_ms": 100}, markets=["A"]).json()["task"]["id"]
    b = submit(client, {"steps": 1}, markets=["B"]).json()["task"]["id"]
    wait_for(client, a, {"Running"})
    r = client.post(f"/api/tasks/{b}/cancel")
    assert r.json()["status"] == "Cancelled" and r.json()["started_at"] is None
    client.post(f"/api/tasks/{a}/cancel")
    wait_for(client, a, TERMINAL)
    time.sleep(0.5)
    assert client.get(f"/api/tasks/{b}").json()["status"] == "Cancelled"


def test_leaked_child_processes_are_reaped(client):
    tid = submit(client, {"steps": 2, "delay_ms": 3000, "leak_child": True}).json()["task"]["id"]
    leaked = child_pid(client, tid)
    t = wait_for(client, tid, TERMINAL, timeout=60)
    assert t["status"] == "Completed"
    assert wait_dead(leaked)
    assert any("leftover process" in l["message"] for l in logs(client, tid))


# ------------------------------------------------------------------ timeouts
def test_wall_clock_timeout(client):
    tid = submit(client, {"steps": 500, "delay_ms": 100}, automation="demo_short").json()["task"]["id"]
    t = wait_for(client, tid, TERMINAL)
    assert t["status"] == "Failed" and "Timed out" in t["error"]


def test_idle_timeout(client):
    tid = submit(client, {"steps": 1, "delay_ms": 10000}, automation="demo_idle").json()["task"]["id"]
    t = wait_for(client, tid, TERMINAL)
    assert t["status"] == "Failed" and "No output" in t["error"]


# ------------------------------------------------------------------ retry
def test_retry_creates_a_new_task(client):
    tid = submit(client, {"steps": 1, "delay_ms": 0, "fail_at_step": 1}).json()["task"]["id"]
    wait_for(client, tid, TERMINAL)
    r = client.post(f"/api/tasks/{tid}/retry")
    assert r.status_code == 201
    new = r.json()["task"]
    assert new["id"] != tid and new["retry_of"] == tid and new["params"]["fail_at_step"] == 1
    wait_for(client, new["id"], TERMINAL)
    assert client.get(f"/api/tasks/{tid}").json()["status"] == "Failed"      # original untouched


def test_retry_only_for_finished_tasks_and_warns_about_password_failures(client, paths):
    live = submit(client, {"steps": 100, "delay_ms": 100}).json()["task"]["id"]
    wait_for(client, live, {"Running"})
    assert client.post(f"/api/tasks/{live}/retry").status_code == 409
    client.post(f"/api/tasks/{live}/cancel")
    wait_for(client, live, TERMINAL)
    db: Database = client.app_state.db
    with db.session() as s:
        s.get(Task, live).summary = {"failures": [{"market": "Dallas", "kind": "password", "message": "x"}]}
    r = client.post(f"/api/tasks/{live}/retry")
    assert r.status_code == 201 and "Dallas" in r.json()["warnings"][0] and "lock" in r.json()["warnings"][0]
    client.post(f"/api/tasks/{r.json()['task']['id']}/cancel")


# ------------------------------------------------------------------ settings -> runner
def test_headless_setting_reaches_the_runner(client):
    def run(automation="demo", **params):
        tid = submit(client, {"steps": 1, "delay_ms": 0, **params}, automation=automation).json()["task"]["id"]
        t = wait_for(client, tid, TERMINAL)
        return t, "\n".join(l["message"] for l in logs(client, tid))

    t, text = run()
    assert t["headless"] is True and "headless=True" in text
    client.put("/api/settings/headless.demo", json={"value": False})
    t, text = run(note="b")
    assert t["headless"] is False and "headless=False" in text
    client.put("/api/settings/headless.demo_locked", json={"value": True})  # rejected (locked) ...
    t, text = run("demo_locked")                                              # ... and always visible
    assert t["headless"] is False and "headless=False" in text


# ------------------------------------------------------------------ stats
def test_stats_and_listing(client):
    a = submit(client, {"steps": 1, "delay_ms": 0}, markets=["Houston"]).json()["task"]["id"]
    b = submit(client, {"steps": 1, "delay_ms": 0, "fail_at_step": 1}, markets=["Tulsa"]).json()["task"]["id"]
    wait_for(client, a, TERMINAL), wait_for(client, b, TERMINAL)
    st = client.get("/api/stats").json()
    assert (st["total"], st["completed"], st["failed"], st["running"], st["queued"], st["files"]) == (2, 1, 1, 0, 0, 1)
    assert client.get("/api/tasks?status=Failed").json()["total"] == 1
    assert client.get("/api/tasks?q=tulsa").json()["items"][0]["id"] == b
    assert client.get(f"/api/tasks?q=%23{a}").json()["total"] == 1
    assert [t["id"] for t in client.get("/api/tasks").json()["items"]] == [b, a]      # newest first
    assert client.get("/api/logs?level=error").json()


# ------------------------------------------------------------------ restart recovery / shutdown
def seed_task(db, status, pid=None, **kw):
    with db.session() as s:
        t = Task(automation_id="demo", params={"steps": 1, "delay_ms": 0}, markets=kw.pop("markets", ["Houston"]),
                 status=status, runner_pid=pid, dedupe_key=f"seed-{status}-{time.time()}", **kw)
        s.add(t)
        s.flush()
        return t.id


def test_recovery_marks_running_interrupted_never_resumes_and_keeps_queue(paths):
    paths.ensure()
    db = Database(paths.db_file)
    ensure_schema(db.engine, paths.db_file, paths.backups)
    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "--run"])
    running = seed_task(db, "Running", pid=orphan.pid)
    starting = seed_task(db, "Starting")
    cancelling = seed_task(db, "Cancelling")
    queued = seed_task(db, "Queued", markets=["Tulsa"])
    with db.session() as s:
        s.add(MarketLock(market="houston", task_id=running))
    stale = paths.task_scratch / str(running)
    (stale / "screenshots").mkdir(parents=True)
    (stale / "screenshots" / "x.png").write_bytes(b"1")
    db.dispose()

    app = create_app(AppConfig(paths=paths, token=TOKEN), registry=make_registry())
    with TestClient(app, headers=H) as c:
        for tid in (running, starting):
            t = c.get(f"/api/tasks/{tid}").json()
            assert t["status"] == "Interrupted" and "NOT resumed" in t["error"], t
            assert t["runner_pid"] is None and t["finished_at"]
        assert c.get(f"/api/tasks/{cancelling}").json()["status"] == "Cancelled"
        assert wait_for(c, queued, TERMINAL)["status"] == "Completed"         # queue survives restart
        assert c.get(f"/api/tasks/{running}").json()["status"] == "Interrupted"  # still not resumed
    assert wait_dead(orphan.pid)                                              # orphan runner killed
    orphan.wait(timeout=5)
    assert (paths.task_logs / f"task_{running}_artifacts" / "screenshots" / "x.png").is_file()
    assert not stale.exists()


def test_graceful_shutdown_kills_runner_and_marks_interrupted(paths):
    app = create_app(AppConfig(paths=paths, token=TOKEN), registry=make_registry())
    with TestClient(app, headers=H) as c:
        tid = submit(c, {"steps": 300, "delay_ms": 100, "spawn_child": True}).json()["task"]["id"]
        t = wait_for(c, tid, {"Running"})
        runner, grandchild = t["runner_pid"], child_pid(c, tid)
    assert wait_dead(runner) and wait_dead(grandchild)
    db = Database(paths.db_file)
    with db.session() as s:
        row = s.get(Task, tid)
        assert row.status == "Interrupted" and row.finished_at is not None
        assert s.scalars(select(MarketLock)).all() == []
    db.dispose()


def test_runner_exits_when_the_backend_disappears(tmp_path):
    """Runner-side watchdog: if the backend dies, the runner tree must not linger."""
    root = Path(__file__).resolve().parents[1]
    child_code = (
        "import sys, time; sys.path.insert(0, %r)\n"
        "from backend.task_manager.runner import _parent_watchdog\n"
        "_parent_watchdog(int(sys.argv[1])); time.sleep(120)\n" % str(root))
    parent_code = (
        "import subprocess, sys, os, time\n"
        "p = subprocess.Popen([sys.executable, '-c', %r, str(os.getpid())])\n"
        "print(p.pid, flush=True); time.sleep(120)\n" % child_code)
    parent = subprocess.Popen([sys.executable, "-c", parent_code], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline())
    time.sleep(1.0)
    assert not dead(child)
    parent.kill(); parent.wait()
    assert wait_dead(child, timeout=15)
