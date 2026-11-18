from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field

class TaskCreate(BaseModel):
    automation: str
    params: dict[str, Any] = Field(default_factory=dict)
    markets: list[str] = Field(default_factory=list)

class TaskSummary(BaseModel):
    id: str
    automation: str
    params: dict[str, Any]
    markets: list[str]
    status: str
    stage: str
    progress: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    runner_pid: int | None

class LogItem(BaseModel):
    timestamp: datetime
    level: str
    message: str
