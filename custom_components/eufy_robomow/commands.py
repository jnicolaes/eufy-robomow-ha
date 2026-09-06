"""Confirm commands using fresh local telemetry, never cloud or cached values."""

from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
from typing import Any

from homeassistant.util import dt as dt_util

from .const import DP_PAUSED, DP_PROGRESS, DP_TASK_ACTIVE, RETURNING_THRESHOLD


def command_evidence(action: str, dps: dict[str, Any]) -> str | None:
    """Describe exactly what a new device status proves."""
    active, paused, progress = dps.get(DP_TASK_ACTIVE), dps.get(DP_PAUSED), dps.get(DP_PROGRESS)
    if action in ("start", "resume"):
        if active is True and paused is False and progress == 0:
            return "mowing_reported"
    elif action == "pause" and active is True and paused is True:
        return "pause_reported"
    elif action == "dock":
        if active is True and isinstance(progress, int) and RETURNING_THRESHOLD <= progress < 100:
            return "returning_reported"
        if active is False:
            # DP1=False proves an inactive task, not physical arrival at the dock.
            return "task_inactive"
    return None


@dataclass
class MowerCommand:
    """One bounded operation; a later safety command may supersede it."""

    action: str
    after_generation: int
    sent_monotonic: float = float("inf")
    state: str = "sending"
    evidence: str | None = None
    requested_at: str = field(default_factory=lambda: dt_util.utcnow().isoformat())
    finished_at: str | None = None
    event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def finish(self, state: str, evidence: str | None = None) -> None:
        self.state, self.evidence = state, evidence
        self.finished_at = dt_util.utcnow().isoformat()
        self.event.set()

    def observe(self, generation: int, dps: dict[str, Any]) -> None:
        if self.state != "pending" or generation <= self.after_generation:
            return
        if evidence := command_evidence(self.action, dps):
            self.finish("confirmed", evidence)

    def as_dict(self) -> dict[str, Any]:
        return {
            key: getattr(self, key)
            for key in ("action", "state", "evidence", "requested_at", "finished_at")
        }
