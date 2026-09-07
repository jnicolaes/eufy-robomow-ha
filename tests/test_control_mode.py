"""Tests for the physical-control safety boundary."""

from __future__ import annotations

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.eufy_robomow.const import (
    OPERATING_MODE_CONTROL,
    OPERATING_MODE_OBSERVE_ONLY,
)
from custom_components.eufy_robomow.coordinator import EufyMowerCoordinator


def _coordinator_for_mode(operating_mode: str) -> EufyMowerCoordinator:
    coordinator = object.__new__(EufyMowerCoordinator)
    coordinator.operating_mode = operating_mode
    return coordinator


def test_observe_only_rejects_writes() -> None:
    coordinator = _coordinator_for_mode(OPERATING_MODE_OBSERVE_ONLY)

    assert coordinator.control_enabled is False
    with pytest.raises(HomeAssistantError, match="observe-only mode"):
        coordinator._require_control_enabled()


def test_control_mode_allows_writes() -> None:
    coordinator = _coordinator_for_mode(OPERATING_MODE_CONTROL)

    assert coordinator.control_enabled is True
    coordinator._require_control_enabled()
