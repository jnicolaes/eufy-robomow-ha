"""Eufy Robomow — Home Assistant integration for the Eufy E15 robot mower.

Protocol: Tuya local v3.5 over TCP port 6668.
Requires: device_id + local_key (obtainable via eufy-clean-local-key-grabber).

Optional: Eufy account email + password unlock cloud-managed settings
(edge distance, path distance, travel speed, blade speed) via DP155.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_EUFY_EMAIL,
    CONF_EUFY_PASSWORD,
    CONF_OPERATING_MODE,
    DEFAULT_OPERATING_MODE,
)
from .coordinator import EufyMowerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.IMAGE,
    Platform.LAWN_MOWER,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,   # no-op when no cloud client (handled in select.py)
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eufy Robomow from a config entry."""
    email    = entry.data.get(CONF_EUFY_EMAIL, "").strip()
    password = entry.data.get(CONF_EUFY_PASSWORD, "").strip()

    cloud_client = None
    if email and password:
        from .cloud import EufyCloudClient  # lazy import — avoids loading cryptography at startup
        cloud_client = EufyCloudClient(
            email=email,
            password=password,
            device_id=entry.data[CONF_DEVICE_ID],
        )
        _LOGGER.debug("Cloud client created for device %s", entry.data[CONF_DEVICE_ID])

    coordinator = EufyMowerCoordinator(
        hass,
        host=entry.data[CONF_HOST],
        device_id=entry.data[CONF_DEVICE_ID],
        local_key=entry.data[CONF_LOCAL_KEY],
        cloud_client=cloud_client,
        operating_mode=entry.options.get(
            CONF_OPERATING_MODE,
            entry.data.get(CONF_OPERATING_MODE, DEFAULT_OPERATING_MODE),
        ),
    )

    # Initial data fetch — raises ConfigEntryNotReady if the mower is unreachable
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
