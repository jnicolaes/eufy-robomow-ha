"""Tests for secret-safe integration setup logging."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from homeassistant.const import CONF_HOST

import custom_components.eufy_robomow as integration
from custom_components.eufy_robomow import cloud
from custom_components.eufy_robomow.const import (
    CONF_DEVICE_ID,
    CONF_EUFY_EMAIL,
    CONF_EUFY_PASSWORD,
    CONF_LOCAL_KEY,
)


class _FakeCloudClient:
    def __init__(self, **kwargs: str) -> None:
        self.kwargs = kwargs


class _FakeCoordinator:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.data: dict[int, object] = {}

    async def async_config_entry_first_refresh(self) -> None:
        pass

    def async_add_listener(self, _listener):
        return lambda: None


def test_setup_log_does_not_expose_device_id(monkeypatch, caplog, tmp_path: Path) -> None:
    """The debug setup message must not include the cloud device identifier."""
    device_id = "sensitive-device-identifier"
    monkeypatch.setattr(cloud, "EufyCloudClient", _FakeCloudClient)
    monkeypatch.setattr(integration, "EufyMowerCoordinator", _FakeCoordinator)
    monkeypatch.setattr(
        integration, "SessionStore", lambda *args: SimpleNamespace(async_load=AsyncMock())
    )

    hass = SimpleNamespace(
        data={},
        config=SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts))),
        config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock()),
    )
    entry = SimpleNamespace(
        data={
            CONF_EUFY_EMAIL: "owner@example.invalid",
            CONF_EUFY_PASSWORD: "not-a-real-password",
            CONF_DEVICE_ID: device_id,
            CONF_HOST: "192.0.2.1",
            CONF_LOCAL_KEY: "not-a-real-local-key",
        },
        options={},
        entry_id="test-entry",
        async_on_unload=lambda _unsubscribe: None,
    )

    with caplog.at_level(logging.DEBUG, logger=integration.__name__):
        assert asyncio.run(integration.async_setup_entry(hass, entry)) is True

    assert "Cloud client created" in caplog.text
    assert device_id not in caplog.text
