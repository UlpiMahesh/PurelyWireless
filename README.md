# Purely Wireless Desktop — Batch 1 backend core

This batch implements the local backend foundation only. It does not yet contain Electron or React, and it does not connect to the T-Mobile portal.

## Requirements

- Python 3.10+
- Windows 10/11 (development)

## Setup (PowerShell)

```powershell
cd C:\path\to\PurelyWireless
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

If PowerShell blocks script activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal only, or activate through `cmd.exe`.

## Run the backend

```powershell
python -m backend.main
```

The backend binds to `127.0.0.1:8765`. It prints a development token as `PW_BACKEND_TOKEN=...`; keep it private. Data is stored under `%LOCALAPPDATA%\PurelyWireless` unless `PW_DATA_DIR` is set. The token can be fixed for local testing with `$env:PW_API_TOKEN = 'your-local-test-token'`.

## Test the queue

In a second PowerShell window, set the token printed by the backend:

```powershell
$env:PW_API_TOKEN = 'paste-token-here'
Invoke-RestMethod -Headers @{ 'X-App-Token' = $env:PW_API_TOKEN } http://127.0.0.1:8765/api/health
$body = @{ automation = 'demo'; params = @{ steps = 4; delay_seconds = 0.2 }; markets = @() } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -ContentType 'application/json' -Headers @{ 'X-App-Token' = $env:PW_API_TOKEN } -Body $body http://127.0.0.1:8765/api/tasks
Invoke-RestMethod -Headers @{ 'X-App-Token' = $env:PW_API_TOKEN } http://127.0.0.1:8765/api/tasks
```

API documentation endpoints (`/docs`, `/redoc`, and `/openapi.json`) are disabled in this batch. Use the endpoint examples above or inspect `backend/api/routes.py` while developing.

## Tests

```powershell
python -m pytest -q
```

## Batch 1 boundaries

- The `demo` automation exercises queueing, subprocess isolation, JSONL events, progress, logs and completion without portal access.
- Only one runner executes at a time.
- Duplicate active tasks are rejected with HTTP 409.
- Running tasks are marked `Interrupted` on backend restart and are never resumed automatically.
- Cancellation kills the runner process tree on Windows using `taskkill /T /F`.
- Real scraper adapters, output-path migration, Electron, SSE/WebSocket streaming, settings UI and installer are later batches.

## Batch 2 — Electron + React desktop UI (development)

The `frontend/` folder contains the Electron shell and React interface. The UI currently supports Overview, Automations, Task Manager, Files & Downloads, Activity Logs, and Settings with persistent dark/light themes.

### Requirements

- Node.js 20.19+ (Node 26 is also suitable)
- npm
- The project's Python `.venv` with `requirements.txt` installed

### Start the desktop app

Open PowerShell in the project root and run:

```powershell
cd frontend
npm install
npm run dev
```

Electron starts the local FastAPI backend automatically using `..\\.venv\\Scripts\\python.exe` when it exists. Otherwise, it uses `python` from PATH. Do not start a second backend on port 8765 while launching the desktop app.

The current backend still registers only the `demo` automation. The other portal workflows are displayed as integration-pending and are not presented as runnable until their Python adapters are connected. The Windows installer command will be enabled after the packaged-backend work is completed.
