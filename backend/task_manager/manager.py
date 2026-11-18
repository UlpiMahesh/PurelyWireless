from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.entities import Automation, GeneratedFile, MarketLock, Task, TaskLog, utcnow

logger = logging.getLogger(__name__)
ACTIVE = {"Pending", "Queued", "Starting", "Running", "Cancelling"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

class TaskManager:
    def __init__(self, session_factory, project_root: Path):
        self.session_factory = session_factory
        self.project_root = Path(project_root).resolve()
        self.queue_event = asyncio.Event()
        self.worker_task = None
        self.current_process = None
        self.current_task_id = None
        self.stopping = False

    def register_automations(self):
        with self.session_factory() as db:
            if not db.get(Automation, "demo"):
                db.add(Automation(id="demo", name="Demo Automation", description="Tests the task queue and runner without accessing the T-Mobile portal."))
            db.commit()

    def recover(self):
        """Never resume a runner after restart; it may have left a portal session mid-flight."""
        with self.session_factory() as db:
            rows = db.scalars(select(Task).where(Task.status.in_(["Starting", "Running", "Cancelling"]))).all()
            for task in rows:
                task.status = "Interrupted"
                task.stage = "Interrupted by application restart"
                task.finished_at = utcnow()
                task.runner_pid = None
                db.add(TaskLog(task_id=task.id, level="WARNING", message="Task was running when the application stopped; it was not resumed."))
            db.commit()
        self.queue_event.set()

    def start(self):
        if self.worker_task is None:
            self.stopping = False
            self.worker_task = asyncio.create_task(self._worker(), name="pw-task-queue")
            self.queue_event.set()


    async def shutdown(self):
        self.stopping = True

        # Mark the active task Interrupted BEFORE terminating its runner.
        if self.current_task_id:
            with self.session_factory() as db:
                task = db.get(Task, self.current_task_id)

                if task and task.status in {"Starting", "Running", "Cancelling"}:
                    task.status = "Interrupted"
                    task.stage = "Interrupted by application shutdown"
                    task.finished_at = utcnow()
                    task.runner_pid = None

                    db.add(TaskLog(
                        task_id=task.id,
                        level="WARNING",
                        message="Application shutting down. Runner terminated; task will not resume."
                    ))
                    db.commit()

        # Now terminate the subprocess.
        if self.current_process and self.current_process.returncode is None:
            await self._terminate_process(self.current_process)
            await self.current_process.wait()

        self.queue_event.set()

        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
            self.worker_task = None


    def create_task(self, automation: str, params: dict, markets: list[str]):
        with self.session_factory() as db:
            definition = db.get(Automation, automation)
            if not definition or not definition.enabled:
                raise ValueError(f"Automation is not registered or disabled: {automation}")
            normalized = _json(params)
            market_json = _json(markets)
            duplicate = db.scalars(select(Task).where(
                Task.automation == automation,
                Task.params_json == normalized,
                Task.markets_json == market_json,
                Task.status.in_(list(ACTIVE)),
            )).first()
            if duplicate:
                raise DuplicateTaskError(duplicate.id)
            task = Task(id=str(uuid.uuid4()), automation=automation, params_json=normalized,
                        markets_json=market_json, status="Queued", stage="Waiting in queue")
            db.add(task)
            db.add(TaskLog(task_id=task.id, level="INFO", message="Task added to queue"))
            db.commit()
            task_id = task.id
        self.queue_event.set()
        return task_id

    def retry_task(self, task_id: str):
        with self.session_factory() as db:
            old = db.get(Task, task_id)
            if not old:
                raise KeyError(task_id)
            return self.create_task(old.automation, json.loads(old.params_json), json.loads(old.markets_json))

    def cancel_task(self, task_id: str):
        with self.session_factory() as db:
            task = db.get(Task, task_id)
            if not task:
                raise KeyError(task_id)
            if task.status in {"Pending", "Queued"}:
                task.status = "Cancelled"
                task.stage = "Cancelled before start"
                task.finished_at = utcnow()
                db.add(TaskLog(task_id=task.id, level="WARNING", message="Task cancelled before it started"))
            elif task.status in {"Starting", "Running"}:
                task.status = "Cancelling"
                task.stage = "Stopping runner"
                db.add(TaskLog(task_id=task.id, level="WARNING", message="Cancellation requested"))
            else:
                return task.status
            db.commit()
            status = task.status
        if status == "Cancelling" and self.current_task_id == task_id and self.current_process:
            asyncio.create_task(self._terminate_process(self.current_process))
        return status

    async def _worker(self):
        while not self.stopping:
            task_data = self._next_queued()
            if not task_data:
                self.queue_event.clear()
                try:
                    await asyncio.wait_for(self.queue_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                continue
            await self._execute(task_data)

    def _next_queued(self):
        with self.session_factory() as db:
            task = db.scalars(select(Task).where(Task.status == "Queued").order_by(Task.created_at, Task.id)).first()
            if not task:
                return None
            task.status = "Starting"
            task.stage = "Starting runner"
            task.started_at = utcnow()
            db.add(TaskLog(task_id=task.id, level="INFO", message="Starting isolated runner process"))
            data = (task.id, task.automation, task.params_json, task.markets_json)
            db.commit()
            return data

    async def _execute(self, data):
        task_id, automation, params_json, markets_json = data
        self.current_task_id = task_id
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.project_root) + os.pathsep + env.get("PYTHONPATH", "")
        env["PW_TASK_ID"] = task_id
        kwargs = {"cwd": str(self.project_root), "env": env,
                  "stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.STDOUT}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(__import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "backend.runner", "--run", "--automation", automation,
                "--params", params_json, **kwargs)
            self.current_process = process
            cancellation_requested = False
            with self.session_factory() as db:
                task = db.get(Task, task_id)
                if task and task.status == "Cancelling":
                    cancellation_requested = True
                elif task and task.status not in {"Interrupted", "Cancelled"}:
                    task.status = "Running"
                    task.runner_pid = process.pid
                    task.stage = "Runner started"
                db.commit()

            # Cancellation may arrive after the task was marked Starting but
            # before the subprocess handle was assigned. In that case
            # cancel_task() cannot terminate it, so handle it here.
            if cancellation_requested:
                await self._terminate_process(process)
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                self._handle_runner_line(task_id, line.decode("utf-8", errors="replace").strip())
            return_code = await process.wait()
            with self.session_factory() as db:
                task = db.get(Task, task_id)
                if task:
                    if task.status == "Interrupted":
                        pass
                    elif task.status == "Cancelling":
                        task.status = "Cancelled"
                        task.stage = "Cancelled"
                    elif return_code == 0:
                        task.status = "Completed"
                        task.stage = "Completed"
                        task.progress = 100 if task.progress is not None else None
                    else:
                        task.status = "Failed"
                        task.stage = "Failed"
                        task.error = task.error or f"Runner exited with code {return_code}"

                    task.finished_at = utcnow()
                    task.runner_pid = None
                    db.add(TaskLog(task_id=task_id, level="INFO" if task.status == "Completed" else "WARNING", message=f"Task finished with status {task.status}"))
                    db.commit()
        except Exception as exc:
            logger.exception("Runner failed for task %s", task_id)
            with self.session_factory() as db:
                task = db.get(Task, task_id)
                if task:
                    if task.status == "Interrupted":
                        # Shutdown has already recorded the authoritative state.
                        # A late runner exception must not overwrite it.
                        pass
                    elif task.status == "Cancelling":
                        task.status = "Cancelled"
                        task.stage = "Cancelled"
                        task.finished_at = utcnow()
                    else:
                        task.status = "Failed"
                        task.stage = "Failed to start runner"
                        task.error = str(exc)
                        task.finished_at = utcnow()
                    task.runner_pid = None
                    db.add(TaskLog(task_id=task_id, level="ERROR", message=str(exc)))
                    db.commit()
        finally:
            self.current_process = None
            self.current_task_id = None

    def _handle_runner_line(self, task_id, line):
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = {"type": "log", "level": "INFO", "message": line}
        kind = event.get("type")
        with self.session_factory() as db:
            task = db.get(Task, task_id)
            if not task:
                return
            if kind == "stage":
                task.stage = str(event.get("stage", "Working"))[:240]
            elif kind == "progress":
                progress = event.get("progress")
                if isinstance(progress, int) and 0 <= progress <= 100:
                    task.progress = progress
                if event.get("stage"):
                    task.stage = str(event["stage"])[:240]
            elif kind == "file":
                path = Path(event.get("path", "")).expanduser().resolve()
                if path.is_file():
                    db.add(GeneratedFile(id=str(uuid.uuid4()), task_id=task_id, automation=task.automation,
                                         path=str(path), name=path.name, size=path.stat().st_size,
                                         markets_json=task.markets_json, date_range=event.get("date_range")))
            elif kind == "error":
                task.error = str(event.get("message", "Runner error"))
                db.add(TaskLog(task_id=task_id, level="ERROR", message=task.error))
            else:
                level = str(event.get("level", "INFO")).upper()
                message = str(event.get("message", event.get("stage", line)))
                db.add(TaskLog(task_id=task_id, level=level[:16], message=message[:10000]))
            db.commit()

    async def _terminate_process(self, process):
        if process.returncode is not None:
            return
        try:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F",
                                                               stdout=asyncio.subprocess.DEVNULL,
                                                               stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            else:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=4)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            logger.exception("Could not terminate runner process %s", process.pid)

class DuplicateTaskError(Exception):
    def __init__(self, existing_task_id):
        self.existing_task_id = existing_task_id
        super().__init__(f"An identical task is already queued or running: {existing_task_id}")
