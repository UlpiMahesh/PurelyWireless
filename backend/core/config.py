"""Application configuration and the data-folder layout."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from automations.paths import default_data_dir


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def database(self) -> Path: return self.root / "Database"
    @property
    def db_file(self) -> Path: return self.database / "purelywireless.db"
    @property
    def files(self) -> Path: return self.root / "Files"
    @property
    def input(self) -> Path: return self.root / "Input"
    @property
    def logs(self) -> Path: return self.root / "Logs"
    @property
    def task_logs(self) -> Path: return self.logs / "tasks"
    @property
    def backups(self) -> Path: return self.root / "Backups"
    @property
    def temp(self) -> Path: return self.root / "Temp"
    @property
    def task_scratch(self) -> Path: return self.temp / "tasks"
    @property
    def browser_data(self) -> Path: return self.root / "BrowserData"
    @property
    def config(self) -> Path: return self.root / "Config"
    @property
    def credentials_file(self) -> Path: return self.config / "marketlogins.xlsx"

    def ensure(self) -> "Paths":
        for p in (self.database, self.files, self.input, self.logs, self.task_logs,
                  self.backups, self.temp, self.task_scratch, self.browser_data, self.config):
            p.mkdir(parents=True, exist_ok=True)
        return self


@dataclass
class AppConfig:
    paths: Paths
    token: str
    max_concurrency: int = 1              # v1: one task at a time (do not raise yet)
    cors_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        o for o in os.environ.get(
            "PW_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",") if o
    ))
    parent_pid: int | None = None

    @staticmethod
    def default_paths() -> Paths:
        return Paths(default_data_dir())
