"""Regression tests for Eufy Robomow integration options."""

from __future__ import annotations

import asyncio
from types import MappingProxyType
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from custom_components.eufy_robomow import async_setup_entry
from custom_components.eufy_robomow.config_flow import EufyRobomowOptionsFlow
from custom_components.eufy_robomow.const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MAP_CERTIFICATE_FINGERPRINT,
    CONF_MAP_SOURCE_URL,
    CONF_OPERATING_MODE,
    DOMAIN,
    OPERATING_MODE_CONTROL,
)


class _FakeCoordinator:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def async_config_entry_first_refresh(self) -> None:
        pass


def test_options_flow_saves_and_schedules_automatic_reload(tmp_path) -> None:
    async def run_test() -> None:
        hass = HomeAssistant(str(tmp_path))
        hass.config_entries = ConfigEntries(hass, {})
        entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain=DOMAIN,
            title="Synthetic mower",
            data={
                CONF_HOST: "127.0.0.1",
                CONF_DEVICE_ID: "synthetic-device",
                CONF_LOCAL_KEY: "0123456789abcdef",
            },
            options={},
            source="user",
            unique_id="synthetic-device",
            discovery_keys=MappingProxyType({}),
            subentries_data=(),
        )
        hass.config_entries._entries[entry.entry_id] = entry

        async def async_forward_entry_setups(*args: object) -> None:
            pass

        with (
            patch(
                "custom_components.eufy_robomow.EufyMowerCoordinator",
                _FakeCoordinator,
            ),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                async_forward_entry_setups,
            ),
        ):
            await async_setup_entry(hass, entry)

        assert not entry.update_listeners

        flow = EufyRobomowOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id
        result = await flow.async_step_init(
            {
                CONF_OPERATING_MODE: OPERATING_MODE_CONTROL,
                CONF_MAP_SOURCE_URL: "",
                CONF_MAP_CERTIFICATE_FINGERPRINT: "",
            }
        )

        with patch.object(hass.config_entries, "async_schedule_reload") as reload_entry:
            await hass.config_entries.options.async_finish_flow(flow, result)

        assert entry.options[CONF_OPERATING_MODE] == OPERATING_MODE_CONTROL
        reload_entry.assert_called_once_with(entry.entry_id)
        await hass.async_stop()

    asyncio.run(run_test())
