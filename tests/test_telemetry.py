"""Idle omission is distinct from an empty/partial local response."""

from datetime import UTC, datetime, timedelta
from custom_components.eufy_robomow.telemetry import task_active
from custom_components.eufy_robomow.sessions import SessionHistory


def test_idle_omission_requires_complete_status_shape():
    assert task_active({"8": 100, "110": 40, "118": 0}) is False
    assert task_active({}) is None
    assert task_active({"8": 100}) is None
    assert task_active({"8": 100, "110": 40, "118": 40}) is None
    assert task_active({"8": 100, "110": 40, "118": 0, "1": True}) is True


def test_observed_idle_then_start_records_a_real_boundary():
    h = SessionHistory()
    t = datetime(2026, 9, 6, 10, tzinfo=UTC)
    h.observe({"8": 100, "110": 40, "118": 0}, t)
    h.observe({"1": True, "2": False, "118": 0}, t + timedelta(seconds=10))
    assert h.current["start_observed"] is True
    h.observe({}, t + timedelta(seconds=20))
    assert h.current is not None
