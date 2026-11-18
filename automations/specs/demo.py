"""Demo automation spec. Side-effect free: the web server imports this, never the adapter."""
from __future__ import annotations

from pydantic import BaseModel, Field

from automations.registry import AutomationSpec


class DemoParams(BaseModel):
    steps: int = Field(5, ge=1, le=500)
    delay_ms: int = Field(100, ge=0, le=10_000)
    fail_at_step: int | None = Field(None, ge=1)
    spawn_child: bool = False        # child process that dies when cancelled (like a browser)
    leak_child: bool = False         # child that outlives the runner; the manager must reap it
    failures: list[str] = []         # markets to report as failures in the summary
    write_diagnostics: bool = False  # drop screenshots/ + logs/ files (like the legacy scripts do)
    hard_exit: bool = False          # die without a goodbye (simulates a crash)
    note: str = ""


DEMO_SPEC = AutomationSpec(
    id="demo",
    name="Demo automation",
    description="Fake automation for testing the queue, logs, progress and files.",
    category="demo",
    folder="Demo",
    adapter="automations.adapters.demo:run",
    params_model=DemoParams,
    icon="FlaskConical",
    needs=("Markets",),
    requires_markets=True,
    headless_default=True,
    timeout_s=None,
    idle_timeout_s=60,
)
