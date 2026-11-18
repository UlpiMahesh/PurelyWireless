"""SQLAlchemy models. Paths and metadata only - never file contents."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.timeutil import utcnow
from backend.models.base import Base

# Task statuses -------------------------------------------------------------
PENDING, QUEUED, STARTING, RUNNING = "Pending", "Queued", "Starting", "Running"
COMPLETED, FAILED, CANCELLING, CANCELLED, INTERRUPTED = (
    "Completed", "Failed", "Cancelling", "Cancelled", "Interrupted")
ALL_STATUSES = (PENDING, QUEUED, STARTING, RUNNING, COMPLETED, FAILED, CANCELLING, CANCELLED, INTERRUPTED)
ACTIVE_STATUSES = (PENDING, QUEUED, STARTING, RUNNING, CANCELLING)
LIVE_STATUSES = (STARTING, RUNNING, CANCELLING)       # a runner process may exist
TERMINAL_STATUSES = (COMPLETED, FAILED, CANCELLED, INTERRUPTED)


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AutomationRow(Base):
    """Cache of the in-code registry (the code is the source of truth)."""
    __tablename__ = "automations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    automation_id: Mapped[str] = mapped_column(String(64), index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    markets: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), index=True, default=PENDING)
    stage: Mapped[str | None] = mapped_column(String(200), nullable=True)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)       # 0..1, only if a real total exists
    progress_done: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    runner_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # failures / warnings / stats
    dedupe_key: Mapped[str] = mapped_column(String(64), index=True)
    retry_of: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    headless: Mapped[bool | None] = mapped_column(Boolean, nullable=True)       # value used for the run


class TaskLog(Base):
    __tablename__ = "task_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    level: Mapped[str] = mapped_column(String(10), default="info")
    message: Mapped[str] = mapped_column(Text)
    __table_args__ = (Index("ix_task_logs_task_id_id", "task_id", "id"),)


class GeneratedFile(Base):
    __tablename__ = "generated_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    automation_id: Mapped[str] = mapped_column(String(64), index=True)
    path: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(300))
    ext: Mapped[str] = mapped_column(String(16), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    markets: Mapped[list[str]] = mapped_column(JSON, default=list)               # tags
    date_from: Mapped[str | None] = mapped_column(String(10), nullable=True)     # YYYY-MM-DD
    date_to: Mapped[str | None] = mapped_column(String(10), nullable=True)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MarketLock(Base):
    """Per-market lock. Enforced by the task manager now; no parallel UI in v1."""
    __tablename__ = "market_locks"
    market: Mapped[str] = mapped_column(String(100), primary_key=True)           # casefolded
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    locked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
