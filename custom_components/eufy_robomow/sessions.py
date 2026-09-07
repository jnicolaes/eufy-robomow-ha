"""Bounded, restart-safe session history without raw DPS or private geometry."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DP_PAUSED, DP_PROGRESS, RETURNING_THRESHOLD
from .telemetry import task_active

MAX_SESSIONS = 50
MAX_OBSERVATION_GAP = 45


class SessionHistory:
    """Record observed task boundaries; never infer completion from lost connectivity."""

    def __init__(self) -> None:
        self.current: dict[str, Any] | None = None
        self.recent: list[dict[str, Any]] = []
        self._previous_at: datetime | None = None
        self._previous_phase = "unknown"
        self._previous_active: bool | None = None
        self._previous_blob: str | None = None
        self._accept_blob = False

    def load(self, data: dict[str, Any]) -> None:
        self.current = data.get("current")
        self.recent = data.get("recent", [])[:MAX_SESSIONS]
        if self.current:
            self.current["observation_gap"] = True
        # No continuity assumption after a restart.

    def dump(self) -> dict[str, Any]:
        return {"current": self.current, "recent": self.recent}

    def observe(self, dps: dict[str, Any], now: datetime) -> bool:
        from .sensor import _decode_dp113

        active = task_active(dps)
        if type(active) is not bool:
            if self.current:
                self.current["observation_gap"] = True
            self._previous_at = None
            return self.current is not None
        blob = dps.get("113")
        if active and self.current is None:
            self.current = {
                "id": uuid4().hex,
                "started_at": now.isoformat(),
                "start_observed": self._previous_active is False,
                "last_observed_at": now.isoformat(),
                "ended_at": None,
                "end_observed": False,
                "mowing_seconds": 0.0,
                "paused_seconds": 0.0,
                "returning_seconds": 0.0,
                "charging_seconds": 0.0,
                "unknown_seconds": 0.0,
                "pause_count": 0,
                "observation_gap": self._previous_active is not False,
                "area_raw": None,
                "distance_m": None,
                "progress": None,
                "phase": "unknown",
            }
            self._previous_at = None
            self._accept_blob = False
        current = self.current
        if current:
            if self._previous_at is not None:
                delta = max(0.0, (now - self._previous_at).total_seconds())
                if delta > MAX_OBSERVATION_GAP:
                    current["observation_gap"] = True
                    current["unknown_seconds"] += delta
                else:
                    current[f"{self._previous_phase}_seconds"] += delta
            phase = "unknown"
            progress = dps.get(DP_PROGRESS)
            if active:
                if dps.get(DP_PAUSED) is True:
                    phase = "paused"
                elif dps.get(DP_PAUSED) is False:
                    if progress == 100:
                        phase = "charging"
                    elif isinstance(progress, int) and RETURNING_THRESHOLD <= progress < 100:
                        phase = "returning"
                    elif progress == 0:
                        phase = "mowing"
                if phase == "paused" and self._previous_phase != "paused":
                    current["pause_count"] += 1
                # A blob left over from a previous task is not current progress.
                if (
                    blob is not None
                    and self._previous_blob is not None
                    and blob != self._previous_blob
                ):
                    self._accept_blob = True
                if self._accept_blob and blob is not None:
                    total, covered, distance = _decode_dp113(blob)
                    if total is not None and covered is not None:
                        current["area_raw"] = covered
                        current["progress"] = round(min(100, max(0, covered / total * 100)), 1)
                    if distance is not None:
                        current["distance_m"] = distance
            current["phase"] = phase
            current["last_observed_at"] = now.isoformat()
            if not active:
                current["ended_at"] = now.isoformat()
                current["end_observed"] = (
                    self._previous_active is True
                    and self._previous_at is not None
                    and (now - self._previous_at).total_seconds() <= MAX_OBSERVATION_GAP
                )
                self.recent.insert(0, dict(current))
                self.recent = self.recent[:MAX_SESSIONS]
                self.current = None
            self._previous_phase = phase
        self._previous_at, self._previous_active = now, active
        self._previous_blob = blob
        return current is not None


class SessionStore:
    """Persist at most fifty summaries and the current observation."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.history = SessionHistory()
        self.store: Store[dict[str, Any]] = Store(
            hass, 1, f"eufy_robomow.sessions.{entry_id}", serialize_in_event_loop=True
        )

    async def async_load(self) -> None:
        if data := await self.store.async_load():
            self.history.load(data)

    def observe(self, dps: dict[str, Any], now: datetime) -> None:
        if self.history.observe(dps, now):
            self.store.async_delay_save(self.history.dump, 5)

    async def async_save(self) -> None:
        await self.store.async_save(self.history.dump())
