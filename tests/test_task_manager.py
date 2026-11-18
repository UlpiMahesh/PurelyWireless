import asyncio
import json
from pathlib import Path
import pytest
from backend.models.entities import Automation, Task, TaskLog
from backend.task_manager.manager import DuplicateTaskError, TaskManager


def manager_for(session_factory, tmp_path):
    manager = TaskManager(session_factory, Path(__file__).resolve().parents[1])
    manager.register_automations()
    return manager


def test_create_task_and_duplicate_protection(session_factory, tmp_path):
    manager = manager_for(session_factory, tmp_path)
    task_id = manager.create_task("demo", {"steps": 2}, [])
    assert task_id
    with pytest.raises(DuplicateTaskError):
        manager.create_task("demo", {"steps": 2}, [])
    with session_factory() as db:
        task = db.get(Task, task_id)
        assert task.status == "Queued"


def test_different_params_are_not_duplicates(session_factory, tmp_path):
    manager = manager_for(session_factory, tmp_path)
    first = manager.create_task("demo", {"steps": 2}, [])
    second = manager.create_task("demo", {"steps": 3}, [])
    assert first != second


def test_cancel_queued_task(session_factory, tmp_path):
    manager = manager_for(session_factory, tmp_path)
    task_id = manager.create_task("demo", {}, [])
    assert manager.cancel_task(task_id) == "Cancelled"
    with session_factory() as db:
        assert db.get(Task, task_id).status == "Cancelled"


def test_recovery_marks_running_interrupted(session_factory, tmp_path):
    manager = manager_for(session_factory, tmp_path)
    task_id = manager.create_task("demo", {}, [])
    with session_factory() as db:
        task = db.get(Task, task_id)
        task.status = "Running"
        db.commit()
    manager.recover()
    with session_factory() as db:
        assert db.get(Task, task_id).status == "Interrupted"
        assert db.query(TaskLog).filter_by(task_id=task_id).count() >= 1


def test_runner_event_persists_progress_and_logs(session_factory, tmp_path):
    manager = manager_for(session_factory, tmp_path)
    task_id = manager.create_task("demo", {}, [])
    manager._handle_runner_line(task_id, json.dumps({"type": "progress", "progress": 50, "stage": "Halfway"}))
    manager._handle_runner_line(task_id, json.dumps({"type": "log", "message": "hello"}))
    with session_factory() as db:
        task = db.get(Task, task_id)
        assert task.progress == 50
        assert task.stage == "Halfway"
        assert db.query(TaskLog).filter_by(task_id=task_id, message="hello").count() == 1


def test_shutdown_preserves_interrupted_status_after_restart(session_factory, tmp_path):
    manager = manager_for(session_factory, tmp_path)
    task_id = manager.create_task("demo", {"steps": 20}, [])

    # Simulate a task that is currently running.
    with session_factory() as db:
        task = db.get(Task, task_id)
        task.status = "Running"
        task.stage = "Running demo"
        task.runner_pid = 12345
        db.commit()

    # Simulate application shutdown.
    manager.current_task_id = task_id
    asyncio.run(manager.shutdown())

    # Verify shutdown marked the task as Interrupted.
    with session_factory() as db:
        task = db.get(Task, task_id)
        assert task.status == "Interrupted"
        assert task.stage == "Interrupted by application shutdown"
        assert task.finished_at is not None
        assert task.runner_pid is None

        shutdown_log = (
            db.query(TaskLog)
            .filter_by(task_id=task_id)
            .filter(TaskLog.message.like("%Application shutting down%"))
            .first()
        )
        assert shutdown_log is not None

    # Simulate a new backend starting and performing recovery.
    restarted_manager = manager_for(session_factory, tmp_path)
    restarted_manager.recover()

    with session_factory() as db:
        task = db.get(Task, task_id)
        assert task.status == "Interrupted"
        assert task.stage == "Interrupted by application shutdown"


class _FakeStdout:
    async def readline(self):
        return b""


class _FakeProcess:
    pid = 24680
    returncode = None
    stdout = _FakeStdout()

    async def wait(self):
        self.returncode = 1
        return self.returncode


def test_cancel_during_start_terminates_new_runner(session_factory, tmp_path, monkeypatch):
    manager = manager_for(session_factory, tmp_path)
    task_id = manager.create_task("demo", {"steps": 2}, [])

    with session_factory() as db:
        task = db.get(Task, task_id)
        task.status = "Starting"
        db.commit()

    # Simulate the cancellation arriving before the subprocess is assigned.
    assert manager.cancel_task(task_id) == "Cancelling"

    fake_process = _FakeProcess()
    async def fake_create_subprocess_exec(*args, **kwargs):
        return fake_process

    terminated = []
    async def fake_terminate_process(process):
        terminated.append(process.pid)
        process.returncode = 1

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(manager, "_terminate_process", fake_terminate_process)

    asyncio.run(manager._execute((task_id, "demo", '{"steps":2}', '[]')))

    assert terminated == [fake_process.pid]
    with session_factory() as db:
        assert db.get(Task, task_id).status == "Cancelled"


def test_runner_exception_does_not_overwrite_interrupted(session_factory, tmp_path, monkeypatch):
    manager = manager_for(session_factory, tmp_path)
    task_id = manager.create_task("demo", {"steps": 2}, [])

    with session_factory() as db:
        task = db.get(Task, task_id)
        task.status = "Interrupted"
        task.stage = "Interrupted by application shutdown"
        db.commit()

    async def fail_to_start(*args, **kwargs):
        raise OSError("simulated runner startup failure")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_to_start)
    asyncio.run(manager._execute((task_id, "demo", '{"steps":2}', '[]')))

    with session_factory() as db:
        task = db.get(Task, task_id)
        assert task.status == "Interrupted"
        assert task.stage == "Interrupted by application shutdown"
        assert task.error is None
