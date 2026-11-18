"""Single shared paths module for adapters and legacy automation code.

Everything is derived from environment variables that the task runner sets
before it imports any automation code. Nothing here touches the disk at import
time: legacy modules create their own folders (that is the existing behaviour),
but because these paths point into a per-task scratch folder, nothing is ever
written next to the source code or into the install directory.

Environment variables (set by the backend for each runner subprocess):
    PW_DATA_DIR          the PurelyWireless data folder
    PW_TASK_DIR          per-task scratch folder (deleted after the task)
    PW_CREDENTIALS_FILE  path of the xlsx-compatible market logins file
    PW_HEADLESS          "1" / "0"; unset means "use the legacy default"
"""
from __future__ import annotations

import os
from pathlib import Path

APP_FOLDER_NAME = "PurelyWireless"


def default_data_dir() -> Path:
    """%LOCALAPPDATA%\\PurelyWireless on Windows (override with PW_DATA_DIR)."""
    override = os.environ.get("PW_DATA_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_FOLDER_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_FOLDER_NAME


DATA_DIR: Path = default_data_dir()
TASK_DIR: Path = Path(os.environ.get("PW_TASK_DIR") or (DATA_DIR / "Temp" / "adhoc"))

# Where legacy code should write. All of these live inside the task scratch folder.
OUTPUT_DIR: Path = TASK_DIR / "output"
LOG_DIR: Path = TASK_DIR / "logs"
SCREENSHOT_DIR: Path = TASK_DIR / "screenshots"
DOWNLOAD_DIR: Path = TASK_DIR / "downloads"

CREDENTIALS_FILE: Path = Path(
    os.environ.get("PW_CREDENTIALS_FILE") or (DATA_DIR / "Config" / "marketlogins.xlsx")
)


def headless_default(legacy_default: bool) -> bool:
    """Headless flag for a browser launch.

    Returns ``legacy_default`` (the value that was hard-coded in the original
    script) unless the runner exported PW_HEADLESS.
    """
    raw = os.environ.get("PW_HEADLESS")
    if raw is None or raw.strip() == "":
        return legacy_default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
