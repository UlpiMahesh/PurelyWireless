from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Optional

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, computed_field

from backend.core.timeutil import utcnow


def _ser(d: Optional[datetime]) -> Optional[str]:
    return None if d is None else d.isoformat(timespec="milliseconds") + "Z"


UtcDT = Annotated[datetime, PlainSerializer(_ser, return_type=Optional[str], when_used="json")]


class TaskCreate(BaseModel):
    automation_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    markets: list[str] = Field(default_factory=list)
    allow_duplicate: bool = False


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    automation_id: str
    params: dict[str, Any]
    markets: list[str]
    status: str
    stage: Optional[str] = None
    progress: Optional[float] = None
    progress_done: Optional[int] = None
    progress_total: Optional[int] = None
    created_at: UtcDT
    started_at: Optional[UtcDT] = None
    finished_at: Optional[UtcDT] = None
    error: Optional[str] = None
    runner_pid: Optional[int] = None
    exit_code: Optional[int] = None
    summary: Optional[dict[str, Any]] = None
    retry_of: Optional[int] = None
    headless: Optional[bool] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_s(self) -> Optional[float]:
        if not self.started_at:
            return None
        end = self.finished_at or utcnow()
        return round((end - self.started_at).total_seconds(), 1)


class TaskCreated(BaseModel):
    task: TaskOut
    warnings: list[str] = Field(default_factory=list)


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    ts: UtcDT
    level: str
    message: str


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    automation_id: str
    path: str
    name: str
    ext: str
    size: int
    created_at: UtcDT
    markets: list[str]
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    exists: bool = True
