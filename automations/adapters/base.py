"""Adapter contract.

An adapter is ``def run(ctx: AdapterContext) -> dict | None``. It normalises the
parameters, calls the legacy function, registers output files and reports
progress. It must not contain business logic of its own.

Summary contract (returned dict, all keys optional)::

    {
      "failures": [{"market": "Dallas", "message": "...", "kind": "password" | "login" | "error" | "missing_credentials"}],
      "warnings": ["Credentials not found for: X, Y"],
      "stats":    {"rows": 120}
    }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class AdapterContext:
    task_id: int
    automation_id: str
    params: dict[str, Any]
    markets: list[str]
    scratch_dir: Path
    data_dir: Path
    credentials_file: Path
    headless: bool
    _emit: Callable[[dict[str, Any]], None] = field(repr=False, default=lambda e: None)

    # -- events -----------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        self._emit({"type": "log", "level": level, "msg": str(message)})

    def stage(self, name: str) -> None:
        self._emit({"type": "stage", "name": str(name)})

    def progress(self, done: int, total: int) -> None:
        """Report progress ONLY when a real total exists. Never invent one."""
        if total and total > 0:
            self._emit({"type": "progress", "done": int(done), "total": int(total)})

    def file(
        self,
        path: str | Path,
        *,
        name: str | None = None,
        markets: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> None:
        """Register an output file (must live inside the task scratch folder)."""
        self._emit({
            "type": "file", "path": str(path), "name": name,
            "markets": list(markets if markets is not None else self.markets),
            "date_from": date_from, "date_to": date_to,
        })
