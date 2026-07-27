"""Switch entities for Eufy Robomow.

Writable boolean DPS:
  • Stop on Rain Detection — DP101 (pause mowing when rain is detected)
  • Child Protection       — DP47  (shield against children/pets running into mower)
  • Smart Suggestions      — DP132 (AI suggestions for no-go zones)
  • Mow Yellow Grass       — DP141 (allow mowing dry/yellow grass)
  • Real Lawn Map          — DP133 (use actual lawn map vs simplified map)
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    DP_RAIN_DETECTION,
    DP_CHILD_PROTECTION,
    DP_SMART_SUGGESTION,
    DP_MOW_YELLOW_GRASS,
    DP_REAL_LAWN_MAP,
)
from .coordinator import EufyMowerCoordinator

# (unique_key, dp_id, display_name, icon, enabled_by_default)
_SWITCHES = (
    ("rain_detection",   DP_RAIN_DETECTION,   "Stop on Rain Detection",  "mdi:weather-rainy",   True),
    ("child_protection", DP_CHILD_PROTECTION, "Child Protection",        "mdi:shield-account",  True),
    ("smart_suggestion", DP_SMART_SUGGESTION, "Smart No-Go Suggestions", "mdi:lightbulb-auto",  True),
    ("mow_yellow_grass", DP_MOW_YELLOW_GRASS, "Mow Yellow Grass",        "mdi:grass",           True),
    ("real_lawn_map",    DP_REAL_LAWN_MAP,     "Real Lawn Map",           "mdi:map",             False),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EufyMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.control_enabled:
        return
    async_add_entities(
        [
            EufySwitch(coordinator, entry, key, dp, name, icon, enabled)
            for key, dp, name, icon, enabled in _SWITCHES
        ]
    )


class EufySwitch(CoordinatorEntity[EufyMowerCoordinator], SwitchEntity):
    """A writable boolean DPS exposed as a HA switch."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EufyMowerCoordinator,
        entry: ConfigEntry,
        key: str,
        dp: str,
        name: str,
        icon: str,
        enabled_by_default: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self._dp = dp
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_entity_registry_enabled_default = enabled_by_default
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_DEVICE_ID])},
        )

    @property
    def is_on(self) -> bool | None:
        val = self.coordinator.data.get(self._dp)
        return bool(val) if val is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self._dp, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_command(self._dp, False)
