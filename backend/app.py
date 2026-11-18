"""FastAPI application factory."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from automations.registry import Registry, build_default_registry
from backend import __version__
from backend.api.routes import api, ws_router
from backend.core.config import AppConfig
from backend.core.errors import install_error_handlers
from backend.core.events import EventBus
from backend.core.timeutil import utcnow
from backend.database.migrations import ensure_schema
from backend.database.session import Database
from backend.models.tables import AutomationRow
from backend.services.settings_service import SettingsService
from backend.task_manager.manager import TaskManager

log = logging.getLogger("pw.app")


def create_app(cfg: AppConfig, registry: Registry | None = None,
               runner_command: Callable[[str], list[str]] | None = None,
               announce: bool = False, dev_token_in_ready_line: bool = False) -> FastAPI:
    registry = registry or build_default_registry()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg.paths.ensure()
        db = Database(cfg.paths.db_file)
        ensure_schema(db.engine, cfg.paths.db_file, cfg.paths.backups)
        with db.session() as s:                       # cache the registry in SQLite
            for spec in registry.all():
                row = s.get(AutomationRow, spec.id)
                data = spec.public()
                if row is None:
                    s.add(AutomationRow(id=spec.id, name=spec.name, spec=data, updated_at=utcnow()))
                else:
                    row.name, row.spec, row.updated_at = spec.name, data, utcnow()
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        settings = SettingsService(db, registry)
        manager = TaskManager(cfg, db, registry, bus, settings, runner_command)
        app.state.db, app.state.bus, app.state.settings, app.state.manager = db, bus, settings, manager
        app.state.registry, app.state.config = registry, cfg
        manager.start()
        if announce:
            line = {"event": "ready", "host": "127.0.0.1", "port": getattr(app.state, "port", None),
                    "pid": os.getpid(), "version": __version__}
            if dev_token_in_ready_line:
                line["dev_token"] = cfg.token
            print(json.dumps(line), flush=True)
        try:
            yield
        finally:
            await asyncio.to_thread(manager.stop)
            db.dispose()

    app = FastAPI(title="PurelyWireless backend", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = cfg
    app.add_middleware(CORSMiddleware, allow_origins=list(cfg.cors_origins), allow_methods=["*"],
                       allow_headers=["x-pw-token", "content-type", "authorization"])
    install_error_handlers(app)
    app.include_router(api)
    app.include_router(ws_router)
    return app
