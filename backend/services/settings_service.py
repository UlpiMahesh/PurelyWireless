from __future__ import annotations

import re
from typing import Any

from backend.core.errors import ApiError
from backend.core.timeutil import utcnow
from backend.database.session import Database
from backend.models.tables import Setting
from automations.registry import AutomationSpec, Registry

THEMES = {"light", "dark", "system"}
_HEADLESS_KEY = re.compile(r"^headless\.([a-z0-9_\-]+)$")


class SettingsService:
    def __init__(self, db: Database, registry: Registry):
        self.db, self.registry = db, registry

    def defaults(self) -> dict[str, Any]:
        d: dict[str, Any] = {"theme": "system", "notifications.enabled": True}
        for spec in self.registry.all():
            d[f"headless.{spec.id}"] = spec.headless_default
        return d

    def get_all(self) -> dict[str, Any]:
        merged = self.defaults()
        with self.db.session() as s:
            for row in s.query(Setting).all():
                if row.key in merged:
                    merged[row.key] = row.value
        return merged

    def set(self, key: str, value: Any) -> Any:
        if key == "theme":
            if value not in THEMES:
                raise ApiError(422, "invalid_setting", f"theme must be one of {sorted(THEMES)}")
        elif key == "notifications.enabled":
            if not isinstance(value, bool):
                raise ApiError(422, "invalid_setting", "notifications.enabled must be true/false")
        elif (m := _HEADLESS_KEY.match(key)):
            spec = self.registry.get(m.group(1))
            if spec is None:
                raise ApiError(404, "unknown_automation", f"Unknown automation: {m.group(1)}")
            if spec.headless_locked:
                raise ApiError(422, "setting_locked", f"{spec.name} must stay visible and cannot be changed.")
            if not isinstance(value, bool):
                raise ApiError(422, "invalid_setting", "headless value must be true/false")
        else:
            raise ApiError(422, "invalid_setting", f"Unknown setting: {key}")
        with self.db.session() as s:
            row = s.get(Setting, key)
            if row is None:
                s.add(Setting(key=key, value=value, updated_at=utcnow()))
            else:
                row.value, row.updated_at = value, utcnow()
        return value

    def resolve_headless(self, spec: AutomationSpec) -> bool:
        """Locked automations always use their built-in value; others follow Settings."""
        if spec.headless_locked:
            return spec.headless_default
        with self.db.session() as s:
            row = s.get(Setting, f"headless.{spec.id}")
        return spec.headless_default if row is None else bool(row.value)
