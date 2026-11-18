from __future__ import annotations

from fastapi import Request

from backend.database.session import Database
from backend.services.settings_service import SettingsService
from backend.task_manager.manager import TaskManager


def get_manager(request: Request) -> TaskManager:
    return request.app.state.manager


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_settings(request: Request) -> SettingsService:
    return request.app.state.settings
