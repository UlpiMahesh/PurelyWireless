"""Automation Registry.

Adding a new automation later = write an adapter + register one AutomationSpec
here. Nothing else in the backend needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class AutomationSpec:
    id: str
    name: str
    description: str
    category: str                      # "market" | "orders" | "demo" ...
    folder: str                        # folder name under Files\<folder>\...
    adapter: str                       # "module.path:function"
    params_model: type[BaseModel]
    icon: str = "Play"                 # Lucide icon name for the UI
    needs: tuple[str, ...] = ()        # human text: "Markets", "Date range", "Order Excel file"
    requires_markets: bool = True
    requires_file: bool = False
    headless_default: bool = True      # = current behaviour of the legacy script
    headless_locked: bool = False      # True => always visible/ignores settings
    timeout_s: int | None = None       # hard wall-clock limit
    idle_timeout_s: int | None = 1800  # kill if no output at all for this long
    enabled: bool = True

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "category": self.category, "folder": self.folder, "icon": self.icon,
            "needs": list(self.needs), "requires_markets": self.requires_markets,
            "requires_file": self.requires_file,
            "headless_default": self.headless_default,
            "headless_locked": self.headless_locked,
            "timeout_s": self.timeout_s, "idle_timeout_s": self.idle_timeout_s,
            "enabled": self.enabled,
            "params_schema": self.params_model.model_json_schema(),
        }


class ParamError(ValueError):
    """Parameters failed validation. ``errors`` is JSON friendly."""

    def __init__(self, errors: list[dict[str, Any]]):
        super().__init__("; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in errors))
        self.errors = errors


class Registry:
    def __init__(self) -> None:
        self._specs: dict[str, AutomationSpec] = {}

    def register(self, spec: AutomationSpec) -> AutomationSpec:
        if spec.id in self._specs:
            raise ValueError(f"Automation already registered: {spec.id}")
        self._specs[spec.id] = spec
        return spec

    def get(self, automation_id: str) -> AutomationSpec | None:
        return self._specs.get(automation_id)

    def all(self) -> list[AutomationSpec]:
        return list(self._specs.values())

    def validate_params(self, automation_id: str, params: dict[str, Any]) -> dict[str, Any]:
        spec = self._specs[automation_id]
        try:
            model = spec.params_model(**(params or {}))
        except ValidationError as exc:
            raise ParamError(exc.errors(include_url=False, include_context=False)) from exc
        return model.model_dump(mode="json")


def build_default_registry(extra: Callable[["Registry"], None] | None = None) -> Registry:
    """The registry the app runs with. Real automations are added in Batches 3a-3c."""
    from automations.specs.demo import DEMO_SPEC

    reg = Registry()
    reg.register(DEMO_SPEC)
    if extra:
        extra(reg)
    return reg
