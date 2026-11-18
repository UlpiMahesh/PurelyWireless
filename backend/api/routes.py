import json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pathlib import Path
from sqlalchemy import func, select
from backend.models.entities import GeneratedFile, Task, TaskLog
from backend.schemas.tasks import TaskCreate
from backend.task_manager.manager import DuplicateTaskError

router = APIRouter()


def get_manager(request: Request):
    return request.app.state.task_manager

def get_db(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()

@router.get("/health")
def health(request: Request):
    return {"status": "ok", "service": "PurelyWireless", "queue_running": request.app.state.task_manager.worker_task is not None}

@router.get("/automations")
def automations(db=Depends(get_db)):
    from backend.models.entities import Automation
    return [{"id": a.id, "name": a.name, "description": a.description, "enabled": a.enabled}
            for a in db.scalars(select(Automation).order_by(Automation.name)).all()]

@router.post("/tasks", status_code=201)
def create_task(payload: TaskCreate, manager=Depends(get_manager)):
    try:
        task_id = manager.create_task(payload.automation, payload.params, payload.markets)
        return {"id": task_id, "status": "Queued"}
    except DuplicateTaskError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "existing_task_id": exc.existing_task_id})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.get("/tasks")
def list_tasks(limit: int = 100, db=Depends(get_db)):
    limit = max(1, min(limit, 500))
    tasks = db.scalars(select(Task).order_by(Task.created_at.desc()).limit(limit)).all()
    return [_task_dict(t) for t in tasks]

@router.get("/tasks/{task_id}")
def task_detail(task_id: str, db=Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    data = _task_dict(task)
    data["logs"] = [{"timestamp": x.timestamp.isoformat(), "level": x.level, "message": x.message}
                     for x in db.scalars(select(TaskLog).where(TaskLog.task_id == task_id).order_by(TaskLog.timestamp)).all()]
    data["files"] = [{"id": f.id, "name": f.name, "path": f.path, "size": f.size}
                      for f in db.scalars(select(GeneratedFile).where(GeneratedFile.task_id == task_id)).all()]
    return data


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, manager=Depends(get_manager)):
    try:
        return {"id": task_id, "status": manager.cancel_task(task_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")


@router.post("/tasks/{task_id}/retry", status_code=201)
def retry_task(task_id: str, manager=Depends(get_manager)):
    try:
        return {"id": manager.retry_task(task_id), "status": "Queued"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")
    except DuplicateTaskError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "existing_task_id": exc.existing_task_id})

@router.get("/activity")
def activity(limit: int = 100, db=Depends(get_db)):
    limit = max(1, min(limit, 500))
    rows = db.scalars(select(TaskLog).order_by(TaskLog.timestamp.desc()).limit(limit)).all()
    return [{"id": row.id, "task_id": row.task_id, "timestamp": row.timestamp.isoformat(),
             "level": row.level, "message": row.message} for row in rows]

@router.get("/files")
def list_files(limit: int = 200, db=Depends(get_db)):
    limit = max(1, min(limit, 500))
    rows = db.scalars(select(GeneratedFile).order_by(GeneratedFile.created_at.desc()).limit(limit)).all()
    return [{"id": row.id, "task_id": row.task_id, "automation": row.automation,
             "name": row.name, "path": row.path, "size": row.size,
             "created_at": row.created_at.isoformat(), "date_range": row.date_range}
            for row in rows]

@router.get("/files/{file_id}/download")
def download_file(file_id: str, db=Depends(get_db)):
    row = db.get(GeneratedFile, file_id)
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    path = Path(row.path).resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Generated file is no longer available")
    return FileResponse(path, filename=row.name)

@router.get("/overview")
def overview(db=Depends(get_db)):
    counts = {status: db.scalar(select(func.count()).select_from(Task).where(Task.status == status)) or 0
              for status in ["Pending", "Queued", "Starting", "Running", "Completed", "Failed", "Cancelled", "Interrupted"]}
    counts["files_generated"] = db.scalar(select(func.count()).select_from(GeneratedFile)) or 0
    return counts


def _task_dict(task):
    return {"id": task.id, "automation": task.automation, "params": json.loads(task.params_json),
            "markets": json.loads(task.markets_json), "status": task.status, "stage": task.stage,
            "progress": task.progress, "created_at": task.created_at.isoformat(),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            "error": task.error, "runner_pid": task.runner_pid}
