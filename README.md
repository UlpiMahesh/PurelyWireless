# Purely Wireless Desktop

Windows desktop app (Electron + React + FastAPI + Playwright) replacing the Streamlit tool.
Full documentation arrives in Batch 8. This README covers the backend as of Batch 1.

## Backend quick start (dev)

    python -m venv .venv
    .venv\Scripts\Activate.ps1
    pip install -r requirements-dev.txt
    python -m pytest -q
    python -m backend.main --dev        # prints a JSON "ready" line with port + dev_token

Data lives in `%LOCALAPPDATA%\PurelyWireless` (override with `--data-dir` or `PW_DATA_DIR`).

## Layout

    backend/      FastAPI app, SQLite, task manager, file manager
    automations/  registry, specs (side-effect free), adapters, legacy (Batch 3)
    tests/        pytest suite
