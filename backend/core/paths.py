from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "PurelyWireless"

@dataclass(frozen=True)
class AppPaths:
    root: Path
    database: Path
    files: Path
    input: Path
    logs: Path
    backups: Path
    browser_data: Path
    temp: Path
    config: Path

    @classmethod
    def from_root(cls, root: Path | str) -> "AppPaths":
        base = Path(root).expanduser().resolve()
        return cls(base, base / "Database", base / "Files", base / "Input",
                   base / "Logs", base / "Backups", base / "BrowserData",
                   base / "Temp", base / "Config")

    def ensure(self) -> "AppPaths":
        for path in (self.root, self.database, self.files, self.input, self.logs,
                     self.backups, self.browser_data, self.temp, self.config):
            path.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def database_file(self) -> Path:
        return self.database / "purelywireless.db"


def get_paths() -> AppPaths:
    configured = os.environ.get("PW_DATA_DIR")
    if configured:
        root = Path(configured)
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / APP_NAME
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP_NAME
    return AppPaths.from_root(root).ensure()
