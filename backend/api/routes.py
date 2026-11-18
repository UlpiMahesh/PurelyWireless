from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from backend import __version__
from backend.api.deps import get_db, get_manager, get_settings
from backend.core.errors import ApiError
from backend.core.security import require_token, websocket_token_ok
from backend.core.timeutil import utcnow
from backend.database.migrations import CURRENT_VERSION
from backend.database.session import Database
from backend.file_manager.service import list_files
from backend.models.tables import GeneratedFile, TaskLog
from backend.schemas.dto import FileOut, LogOut, TaskCreate, TaskCreated, TaskOut
from backend.services.settings_service import SettingsService
from backend.task_manager.manager import TaskManager

api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
ws_router = APIRouter()
_STARTED = time.time()


# ---------------------------------------------------------------- health / stats
@api.get("/health")
def health(request: Request, mgr: TaskManager = Depends(get_manager)) -> dict[str, Any]:
    st = mgr.stats()
    return {
        "status": "ok", "version": __version__, "schema_version": CURRENT_VERSION,
        "uptime_s": round(time.time() - _STARTED, 1),
        "data_dir": str(request.app.state.config.paths.root),
        "running_task_id": st["running_task_id"], "queued": st["queued"],
        "server_time": utcnow().isoformat(timespec="seconds") + "Z",
    }


@api.get("/stats")
def stats(mgr: TaskManager = Depends(get_manager)) -> dict[str, Any]:
    return mgr.stats()


# ---------------------------------------------------------------- automations
@api.get("/automations")
def automations(request: Request) -> list[dict[str, Any]]:
    return [s.public() for s in request.app.state.registry.all()]


@api.get("/automations/{automation_id}")
def automation(automation_id: str, request: Request) -> dict[str, Any]:
    spec = request.app.state.registry.get(automation_id)
    if spec is None:
        raise ApiError(404, "unknown_automation", f"Unknown automation: {automation_id}")
    return spec.public()


# ---------------------------------------------------------------- tasks
@api.post("/tasks", status_code=201, response_model=TaskCreated)
def create_task(body: TaskCreate, mgr: TaskManager = Depends(get_manager)) -> TaskCreated:
    t = mgr.create_task(body.automation_id, body.params, body.markets, body.allow_duplicate)
    return TaskCreated(task=TaskOut.model_validate(t))


@api.get("/tasks")
def tasks(status: str | None = None, automation_id: str | None = None, q: str | None = None,
          limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
          mgr: TaskManager = Depends(get_manager)) -> dict[str, Any]:
    rows, total = mgr.list_tasks(statuses=[x for x in (status or "").split(",") if x] or None,
                                 automation_id=automation_id, q=q, limit=limit, offset=offset)
    return {"total": total, "items": [TaskOut.model_validate(r).model_dump(mode="json") for r in rows]}


@api.get("/tasks/{task_id}", response_model=TaskOut)
def task(task_id: int, mgr: TaskManager = Depends(get_manager)):
    t = mgr.get_task(task_id)
    if t is None:
        raise ApiError(404, "not_found", f"Task #{task_id} not found.")
    return t


@api.post("/tasks/{task_id}/cancel", response_model=TaskOut)
def cancel(task_id: int, mgr: TaskManager = Depends(get_manager)):
    return mgr.cancel_task(task_id)


@api.post("/tasks/{task_id}/retry", status_code=201, response_model=TaskCreated)
def retry(task_id: int, allow_duplicate: bool = False, mgr: TaskManager = Depends(get_manager)) -> TaskCreated:
    t, warnings = mgr.retry_task(task_id, allow_duplicate)
    return TaskCreated(task=TaskOut.model_validate(t), warnings=warnings)


# ---------------------------------------------------------------- logs
@api.get("/logs")
def logs(task_id: int | None = None, level: str | None = None, q: str | None = None,
         after_id: int | None = None, before_id: int | None = None,
         order: str = Query("asc", pattern="^(asc|desc)$"), limit: int = Query(500, ge=1, le=2000),
         db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    with db.session() as s:
        stmt = select(TaskLog)
        if task_id is not None:
            stmt = stmt.where(TaskLog.task_id == task_id)
        if level:
            stmt = stmt.where(TaskLog.level.in_([x for x in level.split(",") if x]))
        if q:
            stmt = stmt.where(TaskLog.message.ilike(f"%{q}%"))
        if after_id is not None:
            stmt = stmt.where(TaskLog.id > after_id)
        if before_id is not None:
            stmt = stmt.where(TaskLog.id < before_id)
        stmt = stmt.order_by(TaskLog.id.asc() if order == "asc" else TaskLog.id.desc()).limit(limit)
        return [LogOut.model_validate(r).model_dump(mode="json") for r in s.scalars(stmt)]


@api.get("/tasks/{task_id}/logs")
def task_logs(task_id: int, level: str | None = None, after_id: int | None = None,
              limit: int = Query(1000, ge=1, le=2000), db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    return logs(task_id=task_id, level=level, q=None, after_id=after_id, before_id=None,
                order="asc", limit=limit, db=db)


# ---------------------------------------------------------------- files
def _file_out(gf: GeneratedFile) -> dict[str, Any]:
    out = FileOut.model_validate(gf).model_dump(mode="json")
    out["exists"] = Path(gf.path).exists()
    return out


@api.get("/files")
def files(automation_id: str | None = None, market: str | None = None, q: str | None = None,
          ext: str | None = None, date_from: str | None = None, date_to: str | None = None,
          task_id: int | None = None, sort: str = Query("created", pattern="^(created|name|size)$"),
          order: str = Query("desc", pattern="^(asc|desc)$"), limit: int = Query(100, ge=1, le=500),
          offset: int = Query(0, ge=0), db: Database = Depends(get_db)) -> dict[str, Any]:
    with db.session() as s:
        rows, total = list_files(s, automation_id=automation_id, market=market, q=q, ext=ext,
                                 date_from=date_from, date_to=date_to, task_id=task_id,
                                 sort=sort, order=order, limit=limit, offset=offset)
        return {"total": total, "items": [_file_out(r) for r in rows]}


@api.get("/files/{file_id}")
def file_one(file_id: int, db: Database = Depends(get_db)) -> dict[str, Any]:
    with db.session() as s:
        gf = s.get(GeneratedFile, file_id)
        if gf is None:
            raise ApiError(404, "not_found", f"File #{file_id} not found.")
        return _file_out(gf)


# ---------------------------------------------------------------- settings
@api.get("/settings")
def get_settings_all(svc: SettingsService = Depends(get_settings)) -> dict[str, Any]:
    return svc.get_all()


@api.put("/settings/{key}")
def put_setting(key: str, value: Any = Body(..., embed=True), svc: SettingsService = Depends(get_settings)) -> dict[str, Any]:
    svc.set(key, value)
    return svc.get_all()


# ---------------------------------------------------------------- websocket
@ws_router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    if not websocket_token_ok(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    bus = ws.app.state.bus
    q = bus.subscribe()
    recv = asyncio.create_task(ws.receive_text())
    getter = asyncio.create_task(q.get())
    try:
        await ws.send_json({"type": "hello", "server_time": utcnow().isoformat(timespec="seconds") + "Z"})
        while True:
            done, _ = await asyncio.wait({recv, getter}, return_when=asyncio.FIRST_COMPLETED)
            if recv in done:
                recv.result()                       # raises WebSocketDisconnect when the client leaves
                recv = asyncio.create_task(ws.receive_text())   # ignore client messages
            if getter in done:
                await ws.send_json(getter.result())
                getter = asyncio.create_task(q.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        recv.cancel()
        getter.cancel()
        bus.unsubscribe(q)
