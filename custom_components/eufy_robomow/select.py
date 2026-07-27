"""Select entities for Eufy Robomow — cloud settings."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    CLOUD_PATH_MM,
    PATH_DISTANCE_MM,
    PATH_DISTANCE_OPTIONS,
    SPEED_OPTIONS,
)
from .coordinator import EufyMowerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities — cloud settings only (require cloud credentials)."""
    coordinator: EufyMowerCoordinator = hass.data[DOMAIN][entry.entry_id]

    if coordinator.control_enabled and coordinator.cloud_client is not None:
        async_add_entities(
            [
                EufyPathDistanceSelect(coordinator, entry),
                EufyTravelSpeedSelect(coordinator, entry),
                EufyBladeSpeedSelect(coordinator, entry),
            ]
        )


class EufyCloudSelect(SelectEntity):
    """Base class for cloud setting selects."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: EufyMowerCoordinator,
        entry: ConfigEntry,
        setting_name: str,
        options: list[str],
    ) -> None:
        super().__init__()
        self._coordinator = coordinator
        self._entry = entry
        self._setting_name = setting_name
        # _attr_options is the HA-standard attribute SelectEntity exposes via the
        # `options` property; setting it here satisfies the framework requirement.
        self._attr_options = list(options)

    @property
    def unique_id(self) -> str:
        return f"{self._entry.data[CONF_DEVICE_ID]}_{self._setting_name}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.data[CONF_DEVICE_ID])},
        )

    @property
    def current_option(self) -> str | None:
        """Return the current setting value."""
        cloud_key = f"cloud_{self._setting_name}"
        return self._coordinator.data.get(cloud_key) if self._coordinator.data else None

    async def async_select_option(self, option: str) -> None:
        """Set the new option."""
        await self._coordinator.async_set_cloud_setting(**{self._setting_name: option})

    def _handle_update(self) -> None:
        """Handle coordinator update."""
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register for coordinator updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_update))


class EufyTravelSpeedSelect(EufyCloudSelect):
    """Travel speed select entity (DP155 field 2)."""

    _attr_name = "Travel Speed"

    def __init__(self, coordinator: EufyMowerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "travel_speed", SPEED_OPTIONS)


class EufyBladeSpeedSelect(EufyCloudSelect):
    """Blade speed select entity (DP155 field 6)."""

    _attr_name = "Blade Speed"

    def __init__(self, coordinator: EufyMowerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "blade_speed", SPEED_OPTIONS)


class EufyPathDistanceSelect(EufyCloudSelect):
    """Path distance select entity — 8 / 10 / 12 cm (DP155 field 5)."""

    _attr_name = "Path Distance"

    def __init__(self, coordinator: EufyMowerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "path_mm", PATH_DISTANCE_OPTIONS)

    # Reverse lookup: PATH_DISTANCE_MM maps label → mm; build mm → label here.
    _MM_TO_LABEL: dict[int, str] = {v: k for k, v in PATH_DISTANCE_MM.items()}

    @property
    def current_option(self) -> str | None:
        """Convert stored mm value back to the human-readable label."""
        if not self._coordinator.data:
            return None
        mm = self._coordinator.data.get(CLOUD_PATH_MM)
        if mm is None:
            return None
        return self._MM_TO_LABEL.get(int(mm))  # None if stored value ∉ {80,100,120}

    async def async_select_option(self, option: str) -> None:
        """Convert label → mm before writing to cloud."""
        mm = PATH_DISTANCE_MM[option]
        await self._coordinator.async_set_cloud_setting(path_mm=mm)
