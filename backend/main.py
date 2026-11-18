from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router
from backend.core.logging_config import configure_logging
from backend.core.paths import get_paths
from backend.database.session import initialize_database, make_engine, make_session_factory
from backend.task_manager.manager import TaskManager

PROJECT_ROOT = Path(__file__).resolve().parents[1]

@asynccontextmanager
async def lifespan(app: FastAPI):
    token = os.environ.get("PW_API_TOKEN") or secrets.token_urlsafe(32)
    app.state.api_token = token
    print(f"PW_BACKEND_TOKEN={token}", flush=True)
    paths = get_paths()
    configure_logging(paths.logs)
    engine = make_engine(paths.database_file)
    initialize_database(engine)
    session_factory = make_session_factory(engine)
    manager = TaskManager(session_factory, PROJECT_ROOT)
    manager.register_automations()
    manager.recover()
    app.state.paths = paths
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.task_manager = manager
    manager.start()
    logging.getLogger(__name__).info("Backend data directory: %s", paths.root)
    yield
    await manager.shutdown()
    engine.dispose()

app = FastAPI(title="Purely Wireless Backend", version="0.1.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=False, allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])


@app.middleware("http")
async def token_guard(request: Request, call_next):
    # Allow browser CORS preflight requests through.
    if request.method == "OPTIONS":
        return await call_next(request)

    expected = os.environ.get("PW_API_TOKEN") or app.state.api_token
    supplied = request.headers.get("X-App-Token")

    if not supplied or not secrets.compare_digest(supplied, expected):
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing or invalid application token"},
        )

    return await call_next(request)


app.include_router(router, prefix="/api")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=int(os.environ.get("PW_PORT", "8765")), reload=False)
