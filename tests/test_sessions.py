"""Session boundaries, interrupted observation, stale telemetry and persistence."""

import asyncio
from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant

from custom_components.eufy_robomow.sessions import SessionHistory, SessionStore

NOW = datetime(2026, 9, 6, 10, tzinfo=UTC)


def test_pause_and_missing_task_flag_do_not_end_session():
    h = SessionHistory()
    h.observe({"1": False}, NOW)
    h.observe({"1": True, "2": False, "118": 0}, NOW + timedelta(seconds=10))
    h.observe({"1": True, "2": True}, NOW + timedelta(seconds=20))
    h.observe({}, NOW + timedelta(seconds=30))
    assert h.current is not None
    assert h.current["pause_count"] == 1
    assert not h.recent
    h.observe({"1": False}, NOW + timedelta(seconds=40))
    assert h.current is None
    assert h.recent[0]["mowing_seconds"] == 10
    assert h.recent[0]["observation_gap"] is True
    assert h.recent[0]["end_observed"] is False


def test_long_gap_does_not_count_as_mowing():
    h = SessionHistory()
    h.observe({"1": True, "2": False, "118": 0}, NOW)
    h.observe({"1": True, "2": False, "118": 0}, NOW + timedelta(hours=1))
    assert h.current["mowing_seconds"] == 0
    assert h.current["unknown_seconds"] == 3600
    assert h.current["start_observed"] is False


def test_stale_session_blob_is_not_a_new_session_measurement():
    h = SessionHistory()
    old = "EAoYBigK"  # total=10, covered=6, distance=10; synthetic
    h.observe({"1": False, "113": old}, NOW)
    h.observe({"1": True, "2": False, "118": 0, "113": old}, NOW + timedelta(seconds=10))
    assert h.current["progress"] is None
    h.observe({"1": True, "2": False, "118": 0, "113": "EAoYBygL"}, NOW + timedelta(seconds=20))
    assert h.current["progress"] == 70
    assert h.current["distance_m"] == 11


def test_restart_retains_history_without_inventing_continuity(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        store = SessionStore(hass, "synthetic-entry")
        store.history.observe({"1": True, "2": False, "118": 0}, NOW)
        await store.async_save()
        restored = SessionStore(hass, "synthetic-entry")
        await restored.async_load()
        restored.history.observe({"1": False}, NOW + timedelta(hours=1))
        assert len(restored.history.recent) == 1
        assert restored.history.recent[0]["mowing_seconds"] == 0
        assert restored.history.recent[0]["end_observed"] is False
        assert restored.history.recent[0]["observation_gap"] is True
        await hass.async_stop()

    asyncio.run(run())


def test_retention_is_bounded():
    h = SessionHistory()
    for i in range(60):
        h.observe({"1": True}, NOW + timedelta(minutes=i))
        h.observe({"1": False}, NOW + timedelta(minutes=i, seconds=10))
    assert len(h.recent) == 50
