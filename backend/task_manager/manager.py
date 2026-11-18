"""Persistent FIFO task queue (concurrency 1) that runs each task in its own subprocess."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from automations.registry import AutomationSpec, ParamError, Registry
from backend.core.config import AppConfig
from backend.core.errors import ApiError
from backend.core.events import EventBus
from backend.core.process_tree import (ProcKey, descendants_of, kill_keys, kill_tree,
                                       pid_looks_like_runner)
from backend.core.timeutil import utcnow
from backend.database.session import Database
from backend.file_manager.service import UnsafeFileError, place_file, resolve_inside
from backend.models.tables import (ACTIVE_STATUSES, CANCELLED, CANCELLING, COMPLETED, FAILED,
                                   INTERRUPTED, LIVE_STATUSES, PENDING, QUEUED, RUNNING, STARTING,
                                   TERMINAL_STATUSES, GeneratedFile, MarketLock, Task, TaskLog)
from backend.schemas.dto import FileOut, TaskOut
from backend.services.settings_service import SettingsService
from backend.task_manager.protocol import classify_level

log = logging.getLogger("pw.tasks")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC_EXT = {".png", ".jpg", ".jpeg", ".html", ".log", ".txt"}
INTERRUPTED_MSG = ("The app was closed or crashed while this task was running. "
                   "It was NOT resumed automatically (re-logging in could lock accounts); "
                   "start it again if you still need it.")


# --------------------------------------------------------------------------- helpers
def normalize_markets(markets: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in markets or []:
        m = str(m).strip()
        if m and m.casefold() not in seen:
            seen.add(m.casefold())
            out.append(m)
    return out


def make_dedupe_key(automation_id: str, params: dict[str, Any], markets: list[str]) -> str:
    payload = json.dumps(
        {"a": automation_id, "p": params, "m": sorted(m.casefold() for m in markets)},
        sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def default_runner_command(task_file: str) -> list[str]:
    if getattr(sys, "frozen", False):                      # PyInstaller backend.exe doubles as the runner
        return [sys.executable, "--run", task_file]
    return [sys.executable, "-m", "backend.main", "--run", task_file]


def _from_ts(ts: Any) -> datetime:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return utcnow()


@dataclass
class RunState:
    task_id: int
    cancel_evt: threading.Event = field(default_factory=threading.Event)
    reason: str | None = None            # "cancel" | "shutdown"


class LogBatcher:
    """Batches log rows into SQLite + the per-task text file + one WebSocket message."""

    def __init__(self, mgr: "TaskManager", task_id: int):
        self.mgr, self.task_id = mgr, task_id
        self.rows: list[tuple[datetime, str, str]] = []
        self.last = time.monotonic()
        mgr.cfg.paths.task_logs.mkdir(parents=True, exist_ok=True)
        self.fh = open(mgr.cfg.paths.task_logs / f"task_{task_id}.log", "a", encoding="utf-8")

    def add(self, level: str, msg: str, ts: datetime | None = None) -> None:
        self.rows.append((ts or utcnow(), level, msg))
        if len(self.rows) >= 100:
            self.flush()

    def tick(self) -> None:
        if self.rows and time.monotonic() - self.last >= 0.5:
            self.flush()

    def flush(self) -> None:
        self.last = time.monotonic()
        if not self.rows:
            return
        rows, self.rows = self.rows, []
        payload = []
        try:
            with self.mgr.db.session() as s:
                objs = [TaskLog(task_id=self.task_id, ts=t, level=lv, message=m) for t, lv, m in rows]
                s.add_all(objs)
                s.flush()
                payload = [{"id": o.id, "task_id": self.task_id,
                            "ts": o.ts.isoformat(timespec="milliseconds") + "Z",
                            "level": o.level, "message": o.message} for o in objs]
            self.mgr.bus.publish({"type": "task.logs", "task_id": self.task_id, "lines": payload})
        except Exception:  # never let logging kill a run
            log.exception("could not store task logs")
        try:
            for t, lv, m in rows:
                self.fh.write(f"{t.isoformat(timespec='milliseconds')}Z [{lv}] {m}\n")
            self.fh.flush()
        except OSError:
            pass

    def close(self) -> None:
        self.flush()
        try:
            self.fh.close()
        except OSError:
            pass


# --------------------------------------------------------------------------- manager
class TaskManager:
    def __init__(self, cfg: AppConfig, db: Database, registry: Registry, bus: EventBus,
                 settings: SettingsService,
                 runner_command: Callable[[str], list[str]] | None = None):
        self.cfg, self.db, self.registry, self.bus, self.settings = cfg, db, registry, bus, settings
        self._runner_command = runner_command or default_runner_command
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running: dict[int, RunState] = {}

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.recover()
        self._thread = threading.Thread(target=self._worker, name="task-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            for rs in self._running.values():
                rs.reason = "shutdown"
                rs.cancel_evt.set()
        if self._thread:
            self._thread.join(timeout=30)

    def recover(self) -> None:
        """Tasks left running by a crash become Interrupted. NEVER auto-resumed."""
        interrupted: list[int] = []
        with self.db.session() as s:
            for t in s.scalars(select(Task).where(Task.status.in_(LIVE_STATUSES))):
                if t.runner_pid and pid_looks_like_runner(t.runner_pid):
                    kill_tree(t.runner_pid)
                was_cancelling = t.status == CANCELLING
                t.status = CANCELLED if was_cancelling else INTERRUPTED
                t.error = None if was_cancelling else INTERRUPTED_MSG
                t.finished_at, t.runner_pid = utcnow(), None
                s.add(TaskLog(task_id=t.id, level="warning" if not was_cancelling else "info",
                              message="Task cancelled." if was_cancelling else INTERRUPTED_MSG))
                interrupted.append(t.id)
            for t in s.scalars(select(Task).where(Task.status == PENDING)):
                t.status = QUEUED
            s.execute(delete(MarketLock))
        scratch_root = self.cfg.paths.task_scratch
        if scratch_root.exists():
            for d in scratch_root.iterdir():
                if d.is_dir():
                    if d.name.isdigit():
                        self._preserve_diagnostics(int(d.name), d)
                    shutil.rmtree(d, ignore_errors=True)
        for tid in interrupted:
            self._publish_task(tid)

    # ------------------------------------------------------------------ public API
    def create_task(self, automation_id: str, params: dict[str, Any] | None, markets: list[str] | None,
                    allow_duplicate: bool = False, retry_of: int | None = None) -> Task:
        spec = self.registry.get(automation_id)
        if spec is None:
            raise ApiError(404, "unknown_automation", f"Unknown automation: {automation_id}")
        if not spec.enabled:
            raise ApiError(409, "automation_disabled", f"{spec.name} is disabled.")
        markets = normalize_markets(markets)
        if spec.requires_markets and not markets:
            raise ApiError(422, "markets_required", "Select at least one market.")
        try:
            clean = self.registry.validate_params(automation_id, params or {})
        except ParamError as exc:
            raise ApiError(422, "invalid_params", str(exc), errors=exc.errors) from exc

        key = make_dedupe_key(automation_id, clean, markets)
        with self._lock:
            with self.db.session() as s:
                if not allow_duplicate:
                    dup = s.scalar(select(Task).where(Task.dedupe_key == key,
                                                      Task.status.in_(ACTIVE_STATUSES)).limit(1))
                    if dup is not None:
                        raise ApiError(409, "duplicate_task",
                                       f"The same {spec.name} task is already {dup.status.lower()} (#{dup.id}).",
                                       existing_task_id=dup.id)
                t = Task(automation_id=automation_id, params=clean, markets=markets, status=QUEUED,
                         dedupe_key=key, retry_of=retry_of, created_at=utcnow())
                s.add(t)
                s.flush()
                tid = t.id
                s.add(TaskLog(task_id=tid, level="info", message="Task queued."))
        self._publish_task(tid)
        self._wake.set()
        return self.get_task(tid)  # type: ignore[return-value]

    def retry_task(self, task_id: int, allow_duplicate: bool = False) -> tuple[Task, list[str]]:
        old = self.get_task(task_id)
        if old is None:
            raise ApiError(404, "not_found", f"Task #{task_id} not found.")
        if old.status not in TERMINAL_STATUSES:
            raise ApiError(409, "not_retryable", f"Task #{task_id} is {old.status.lower()}; only finished tasks can be retried.")
        warnings: list[str] = []
        pw = [f.get("market") for f in (old.summary or {}).get("failures", []) if f.get("kind") == "password"]
        if pw:
            warnings.append("These markets had PASSWORD failures last time: " + ", ".join(map(str, pw)) +
                            ". Fix the password first - retrying can lock the account.")
        new = self.create_task(old.automation_id, old.params, old.markets, allow_duplicate, retry_of=old.id)
        return new, warnings

    def cancel_task(self, task_id: int) -> Task:
        with self._lock:
            with self.db.session() as s:
                t = s.get(Task, task_id)
                if t is None:
                    raise ApiError(404, "not_found", f"Task #{task_id} not found.")
                if t.status in (QUEUED, PENDING):
                    t.status, t.finished_at = CANCELLED, utcnow()
                    s.add(TaskLog(task_id=task_id, level="info", message="Cancelled while queued."))
                elif t.status in (STARTING, RUNNING):
                    rs = self._running.get(task_id)
                    t.status = CANCELLING
                    s.add(TaskLog(task_id=task_id, level="info", message="Cancel requested."))
                    if rs is not None:
                        rs.reason = rs.reason or "cancel"
                        rs.cancel_evt.set()
                elif t.status == CANCELLING:
                    pass
                else:
                    raise ApiError(409, "not_cancellable", f"Task #{task_id} is already {t.status.lower()}.")
        self._publish_task(task_id)
        return self.get_task(task_id)  # type: ignore[return-value]

    def get_task(self, task_id: int) -> Task | None:
        with self.db.session() as s:
            return s.get(Task, task_id)

    def list_tasks(self, *, statuses: list[str] | None = None, automation_id: str | None = None,
                   q: str | None = None, limit: int = 100, offset: int = 0) -> tuple[list[Task], int]:
        with self.db.session() as s:
            stmt = select(Task)
            cnt = select(func.count()).select_from(Task)
            conds = []
            if statuses:
                conds.append(Task.status.in_(statuses))
            if automation_id:
                conds.append(Task.automation_id == automation_id)
            if q:
                like = f"%{q}%"
                from sqlalchemy import cast, String, or_
                conds.append(or_(Task.automation_id.ilike(like), cast(Task.markets, String).ilike(like),
                                 cast(Task.id, String) == q.lstrip("#")))
            for c in conds:
                stmt, cnt = stmt.where(c), cnt.where(c)
            rows = list(s.scalars(stmt.order_by(Task.id.desc()).limit(limit).offset(offset)))
            return rows, int(s.scalar(cnt) or 0)

    def stats(self) -> dict[str, Any]:
        with self.db.session() as s:
            by = dict(s.execute(select(Task.status, func.count()).group_by(Task.status)).all())
            files = int(s.scalar(select(func.count()).select_from(GeneratedFile)) or 0)
            running = s.scalar(select(Task.id).where(Task.status.in_((STARTING, RUNNING, CANCELLING))).limit(1))
        return {
            "total": sum(by.values()), "by_status": by,
            "running": by.get(RUNNING, 0) + by.get(STARTING, 0) + by.get(CANCELLING, 0),
            "queued": by.get(QUEUED, 0) + by.get(PENDING, 0),
            "completed": by.get(COMPLETED, 0), "failed": by.get(FAILED, 0),
            "files": files, "running_task_id": running,
        }

    # ------------------------------------------------------------------ worker
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                tid = self._next_queued()
            except Exception:
                log.exception("queue poll failed")
                time.sleep(1)
                continue
            if tid is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            try:
                self._execute(tid)
            except Exception as exc:  # manager bug - never leave a task stuck
                log.exception("task %s crashed inside the manager", tid)
                self._finish(tid, FAILED, f"Internal error: {type(exc).__name__}: {exc}", None, None)

    def _next_queued(self) -> int | None:
        with self._lock, self.db.session() as s:
            if len(self._running) >= self.cfg.max_concurrency:
                return None
            held = {m.market for m in s.scalars(select(MarketLock))}
            for t in s.scalars(select(Task).where(Task.status == QUEUED).order_by(Task.id)):
                if any(m.casefold() in held for m in t.markets):
                    continue
                return t.id
        return None

    def _acquire_locks(self, s: Session, t: Task) -> bool:
        keys = [m.casefold() for m in t.markets]
        if not keys:
            return True
        held = {l.market: l.task_id for l in s.scalars(select(MarketLock).where(MarketLock.market.in_(keys)))}
        if any(owner != t.id for owner in held.values()):
            return False
        for k in keys:
            if k not in held:
                s.add(MarketLock(market=k, task_id=t.id))
        return True

    def _execute(self, task_id: int) -> None:
        with self._lock:
            with self.db.session() as s:
                t = s.get(Task, task_id)
                if t is None or t.status != QUEUED:
                    return
                spec = self.registry.get(t.automation_id)
                if spec is None:
                    t.status, t.finished_at = FAILED, utcnow()
                    t.error = f"Automation '{t.automation_id}' is no longer available."
                    return
                if not self._acquire_locks(s, t):
                    return
                headless = self.settings.resolve_headless(spec)
                t.status, t.started_at, t.headless = STARTING, utcnow(), headless
                t.stage = "Starting"
                params, markets = dict(t.params), list(t.markets)
            rs = RunState(task_id)
            self._running[task_id] = rs
        self._publish_task(task_id)

        paths = self.cfg.paths
        scratch = paths.task_scratch / str(task_id)
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True, exist_ok=True)
        task_file = scratch / "task.json"
        task_file.write_text(json.dumps({
            "task_id": task_id, "automation_id": spec.id, "adapter": spec.adapter,
            "params": params, "markets": markets, "scratch_dir": str(scratch),
            "data_dir": str(paths.root), "credentials_file": str(paths.credentials_file),
            "headless": headless, "parent_pid": os.getpid(),
        }), encoding="utf-8")

        env = os.environ.copy()
        env.pop("PW_TOKEN", None)                       # the runner/legacy code never sees the API token
        env.update({
            "PW_DATA_DIR": str(paths.root), "PW_TASK_DIR": str(scratch), "PW_TASK_ID": str(task_id),
            "PW_CREDENTIALS_FILE": str(paths.credentials_file), "PW_HEADLESS": "1" if headless else "0",
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
        })
        if not getattr(sys, "frozen", False):
            env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

        batcher = LogBatcher(self, task_id)
        try:
            proc = subprocess.Popen(
                self._runner_command(str(task_file)), cwd=str(scratch), env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=flags)
        except OSError as exc:
            batcher.add("error", f"Could not start the runner: {exc}")
            batcher.close()
            self._finish(task_id, FAILED, f"Could not start the runner: {exc}", None, None)
            self._cleanup(task_id, scratch)
            return

        with self.db.session() as s:
            s.get(Task, task_id).runner_pid = proc.pid  # type: ignore[union-attr]
        try:
            outcome = self._supervise(task_id, spec, proc, rs, scratch, batcher)
        finally:
            batcher.close()

        killed, ev_done, ev_error, code = outcome["killed"], outcome["done"], outcome["error"], proc.returncode
        if killed == "cancel":
            status, error, summary = CANCELLED, None, None
        elif killed == "shutdown":
            status, error, summary = INTERRUPTED, "The app was closed while this task was running. It was not resumed.", None
        elif killed == "timeout":
            status, error, summary = FAILED, f"Timed out after {spec.timeout_s} s (limit for {spec.name}). The task was stopped.", None
        elif killed == "idle":
            status, error, summary = FAILED, f"No output for {spec.idle_timeout_s} s - the automation looks stuck and was stopped.", None
        elif ev_done is not None:
            status, error, summary = COMPLETED, None, ev_done.get("summary") or {}
        elif ev_error is not None:
            status, error, summary = FAILED, ev_error.get("message") or "Automation failed.", None
        else:
            status, error, summary = FAILED, f"The runner stopped unexpectedly (exit code {code}).", None
        if ev_error is not None and ev_error.get("traceback"):
            with self.db.session() as s:
                s.add(TaskLog(task_id=task_id, level="error", message=ev_error["traceback"]))
        self._finish(task_id, status, error, summary, code)
        self._cleanup(task_id, scratch)

    # ------------------------------------------------------------------ supervision
    def _supervise(self, task_id: int, spec: AutomationSpec, proc: subprocess.Popen,
                   rs: RunState, scratch: Path, batcher: LogBatcher) -> dict[str, Any]:
        q: queue.Queue = queue.Queue()

        def pump(stream, kind: str) -> None:
            try:
                for raw in iter(stream.readline, b""):
                    q.put((kind, raw.decode("utf-8", "replace").rstrip("\r\n")))
            except (OSError, ValueError):
                pass
            finally:
                q.put((kind, None))

        for stream, kind in ((proc.stdout, "out"), (proc.stderr, "err")):
            threading.Thread(target=pump, args=(stream, kind), daemon=True).start()

        out: dict[str, Any] = {"done": None, "error": None, "killed": None}
        prog = {"latest": None, "dirty": False, "last": 0.0}
        seen: set[ProcKey] = set()
        eofs, exited_at, next_snap = 0, None, 0.0
        t0 = last_activity = time.monotonic()

        def handle(kind: str, line: str) -> None:
            if kind == "err":
                batcher.add(classify_level(line, "debug"), line)
                return
            try:
                ev = json.loads(line)
                if not isinstance(ev, dict):
                    raise ValueError
            except ValueError:
                batcher.add("info", line)
                return
            self._on_event(task_id, spec, ev, batcher, out, prog, scratch)

        def kill(reason: str) -> None:
            out["killed"] = reason
            kill_tree(proc.pid, list(seen))
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

        while True:
            try:
                kind, line = q.get(timeout=0.2)
            except queue.Empty:
                pass
            else:
                last_activity = time.monotonic()
                if line is None:
                    eofs += 1
                else:
                    handle(kind, line)
            now = time.monotonic()
            batcher.tick()
            self._flush_progress(task_id, prog, now)

            if out["killed"] is None:
                if rs.cancel_evt.is_set():
                    kill(rs.reason or "cancel")
                elif spec.timeout_s and now - t0 > spec.timeout_s:
                    kill("timeout")
                elif spec.idle_timeout_s and now - last_activity > spec.idle_timeout_s:
                    kill("idle")
            if out["killed"] is None and proc.poll() is None and now >= next_snap:
                seen.update(descendants_of(proc.pid))
                next_snap = now + 2.0

            if proc.poll() is not None:
                exited_at = exited_at or now
                if eofs >= 2 or now - exited_at > 2.0:
                    break

        while True:  # drain anything still queued
            try:
                kind, line = q.get_nowait()
            except queue.Empty:
                break
            if line is not None:
                handle(kind, line)
        self._flush_progress(task_id, prog, time.monotonic() + 10, force=True)

        leftovers = kill_keys(list(seen))
        if leftovers and out["killed"] is None:
            batcher.add("warning", f"Cleaned up {leftovers} leftover process(es) started by the automation.")
        proc.wait()
        return out

    def _flush_progress(self, task_id: int, prog: dict[str, Any], now: float, force: bool = False) -> None:
        if not prog["dirty"] or (not force and now - prog["last"] < 0.2):
            return
        done, total = prog["latest"]
        prog["dirty"], prog["last"] = False, now
        with self.db.session() as s:
            t = s.get(Task, task_id)
            if t is not None:
                t.progress_done, t.progress_total, t.progress = done, total, done / total
        self._publish_task(task_id)

    def _on_event(self, task_id: int, spec: AutomationSpec, ev: dict[str, Any], batcher: LogBatcher,
                  out: dict[str, Any], prog: dict[str, Any], scratch: Path) -> None:
        kind = ev.get("type")
        if kind == "hello":
            with self.db.session() as s:
                t = s.get(Task, task_id)
                if t is not None and t.status == STARTING:
                    t.status = RUNNING
            self._publish_task(task_id)
        elif kind == "log":
            batcher.add(str(ev.get("level") or "info"), str(ev.get("msg", "")), _from_ts(ev.get("ts")))
        elif kind == "stage":
            name = str(ev.get("name", ""))[:200]
            with self.db.session() as s:
                t = s.get(Task, task_id)
                if t is not None:
                    t.stage = name
            batcher.add("info", f"Stage: {name}")
            self._publish_task(task_id)
        elif kind == "progress":
            try:
                total, done = int(ev["total"]), int(ev["done"])
            except (KeyError, TypeError, ValueError):
                return
            if total > 0:
                prog["latest"], prog["dirty"] = (max(0, min(done, total)), total), True
                if done >= total:
                    prog["last"] = 0.0
        elif kind == "file":
            self._register_file(task_id, spec, ev, batcher, scratch)
        elif kind == "done":
            out["done"] = ev
        elif kind == "error":
            out["error"] = ev

    def _register_file(self, task_id: int, spec: AutomationSpec, ev: dict[str, Any],
                       batcher: LogBatcher, scratch: Path) -> None:
        try:
            src = resolve_inside(scratch, str(ev.get("path", "")))
            when = utcnow()
            dest = place_file(self.cfg.paths.files, spec.folder, src, ev.get("name"), task_id, when)
            with self.db.session() as s:
                gf = GeneratedFile(
                    task_id=task_id, automation_id=spec.id, path=str(dest), name=dest.name,
                    ext=dest.suffix.lstrip(".").lower(), size=dest.stat().st_size, created_at=when,
                    markets=normalize_markets(ev.get("markets")),
                    date_from=ev.get("date_from"), date_to=ev.get("date_to"))
                s.add(gf)
                s.flush()
                payload = FileOut.model_validate(gf).model_dump(mode="json")
            self.bus.publish({"type": "file.created", "file": payload})
            batcher.add("info", f"Saved file: {dest.name}")
        except UnsafeFileError as exc:
            batcher.add("error", str(exc))
        except Exception as exc:
            log.exception("file registration failed")
            batcher.add("error", f"Could not save output file: {exc}")

    # ------------------------------------------------------------------ finishing
    def _finish(self, task_id: int, status: str, error: str | None, summary: dict[str, Any] | None,
                exit_code: int | None) -> None:
        with self._lock:
            with self.db.session() as s:
                t = s.get(Task, task_id)
                if t is not None:
                    t.status, t.error, t.exit_code = status, error, exit_code
                    t.finished_at, t.runner_pid = utcnow(), None
                    if summary is not None:
                        t.summary = summary
                    if status == COMPLETED and t.progress_total:
                        t.progress_done, t.progress = t.progress_total, 1.0
                    if error:
                        s.add(TaskLog(task_id=task_id, level="error" if status == FAILED else "warning", message=error))
                    else:
                        s.add(TaskLog(task_id=task_id, level="info", message=f"Task {status.lower()}."))
                s.execute(delete(MarketLock).where(MarketLock.task_id == task_id))
            self._running.pop(task_id, None)
        self._publish_task(task_id)

    def _preserve_diagnostics(self, task_id: int, scratch: Path) -> None:
        """Keep screenshots / legacy log files; reports are registered separately."""
        dest_root = self.cfg.paths.task_logs / f"task_{task_id}_artifacts"
        try:
            for p in list(scratch.rglob("*")):
                if not p.is_file() or p.suffix.lower() not in DIAGNOSTIC_EXT or p.name == "task.json":
                    continue
                rel = p.relative_to(scratch)
                if rel.parts[0] in ("output", "downloads"):
                    continue
                target = dest_root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(target))
        except OSError:
            log.exception("could not preserve diagnostics for task %s", task_id)

    def _cleanup(self, task_id: int, scratch: Path) -> None:
        self._preserve_diagnostics(task_id, scratch)
        shutil.rmtree(scratch, ignore_errors=True)

    def _publish_task(self, task_id: int) -> None:
        t = self.get_task(task_id)
        if t is not None:
            self.bus.publish({"type": "task.updated",
                              "task": TaskOut.model_validate(t).model_dump(mode="json")})
