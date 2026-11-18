from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database.base import Base


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

class Automation(Base):
    __tablename__ = "automations"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    automation: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    markets_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    status: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(240), default="Queued")
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    runner_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logs = relationship("TaskLog", back_populates="task", cascade="all, delete-orphan")
    files = relationship("GeneratedFile", back_populates="task", cascade="all, delete-orphan")

class TaskLog(Base):
    __tablename__ = "task_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    task = relationship("Task", back_populates="logs")

class GeneratedFile(Base):
    __tablename__ = "generated_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    automation: Mapped[str] = mapped_column(String(100), index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(260), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    markets_json: Mapped[str] = mapped_column(Text, default="[]")
    date_range: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    task = relationship("Task", back_populates="files")

class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

class MarketLock(Base):
    __tablename__ = "market_locks"
    __table_args__ = (UniqueConstraint("market", name="uq_market_lock_market"),)
    market: Mapped[str] = mapped_column(String(120), primary_key=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
