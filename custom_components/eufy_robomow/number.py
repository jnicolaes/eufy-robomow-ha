"""Number entities for Eufy Robomow.

Local entities (backed by Tuya DPS):
  • Cut Height      — DP110, 25–75 mm, step 5 mm (slider)

Cloud entities (backed by DP154/DP155 via Tuya mobile API):
  • Edge Distance   — DP155 field 3, -15 to +15 cm, step 1 cm
    (negative = cut beyond wire, positive = stay inside)
  • Pad Direction   — DP154, 0–359°, step 1° (rotary; full rotation)

Path distance is a fixed 3-option select; see select.py.
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    DP_CUT_HEIGHT,
    CUT_HEIGHT_MIN,
    CUT_HEIGHT_MAX,
    CUT_HEIGHT_STEP,
    CLOUD_EDGE_MM,
    EDGE_DISTANCE_MIN,
    EDGE_DISTANCE_MAX,
    EDGE_DISTANCE_STEP,
    CLOUD_PAD_DIRECTION,
    PAD_DIRECTION_MIN,
    PAD_DIRECTION_MAX,
    PAD_DIRECTION_STEP,
)
from .coordinator import EufyMowerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EufyMowerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[NumberEntity] = [EufyCutHeightNumber(coordinator, entry)]

    if coordinator.cloud_client is not None:
        entities.append(EufyEdgeDistanceNumber(coordinator, entry))
        entities.append(EufyPadDirectionNumber(coordinator, entry))

    async_add_entities(entities)


# ── Local entity ───────────────────────────────────────────────────────────────


class EufyCutHeightNumber(CoordinatorEntity[EufyMowerCoordinator], NumberEntity):
    """Adjustable cut height for the Eufy E15 (local DPS 110)."""

    _attr_has_entity_name = True
    _attr_name = "Cut Height"
    _attr_icon = "mdi:ruler"
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_native_min_value = CUT_HEIGHT_MIN
    _attr_native_max_value = CUT_HEIGHT_MAX
    _attr_native_step = CUT_HEIGHT_STEP
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: EufyMowerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_cut_height"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_DEVICE_ID])},
        )

    @property
    def native_value(self) -> float | None:
        val = self.coordinator.data.get(DP_CUT_HEIGHT)
        return float(val) if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_send_command(DP_CUT_HEIGHT, int(value))


# ── Cloud entity ───────────────────────────────────────────────────────────────


class EufyEdgeDistanceNumber(CoordinatorEntity[EufyMowerCoordinator], NumberEntity):
    """How far inside (positive) or outside (negative) the border wire the mower cuts.

    DP155 field 3 stores the value in mm.  This entity exposes it in cm so the
    scale matches the Eufy app (-15 to +15 cm).
    """

    _attr_has_entity_name = True
    _attr_name = "Edge Distance"
    _attr_icon = "mdi:border-outside"
    _attr_native_unit_of_measurement = UnitOfLength.CENTIMETERS
    _attr_native_min_value = EDGE_DISTANCE_MIN  # -15 cm
    _attr_native_max_value = EDGE_DISTANCE_MAX  #  15 cm
    _attr_native_step = EDGE_DISTANCE_STEP  #   1 cm
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: EufyMowerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        device_id = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{device_id}_edge_distance"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        mm = self.coordinator.data.get(CLOUD_EDGE_MM)
        if mm is None:
            return None
        # Convert mm → cm (the app's native unit)
        return mm / 10.0

    @property
    def available(self) -> bool:
        return (
            super().available
            and bool(self.coordinator.data)
            and CLOUD_EDGE_MM in self.coordinator.data
        )

    async def async_set_native_value(self, value: float) -> None:
        """Send new edge distance (cm → mm for cloud)."""
        await self.coordinator.async_set_cloud_setting(edge_mm=round(value * 10))


class EufyPadDirectionNumber(CoordinatorEntity[EufyMowerCoordinator], NumberEntity):
    """Mowing path direction for the Eufy E15.

    DP154 stores the mowing angle as an integer in degrees (0–359).
    The app shows a rotary control for a full rotation.

    Confirmed encoding:
        degrees 0 → b'\\x00'  (base64 "AA==")  — device-native quirk
        degrees N → protobuf field 3 = N       for N > 0
    """

    _attr_has_entity_name = True
    _attr_name = "Pad Direction"
    _attr_icon = "mdi:rotate-right"
    _attr_native_unit_of_measurement = "°"
    _attr_native_min_value = PAD_DIRECTION_MIN  #   0°
    _attr_native_max_value = PAD_DIRECTION_MAX  # 359°
    _attr_native_step = PAD_DIRECTION_STEP  #   1°
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: EufyMowerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        device_id = entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{device_id}_pad_direction"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_id)})

    @property
    def native_value(self) -> float | None:
        if not self.coordinator.data:
            return None
        direction = self.coordinator.data.get(CLOUD_PAD_DIRECTION)
        if direction is None:
            return None
        return float(direction)

    @property
    def available(self) -> bool:
        return (
            super().available
            and bool(self.coordinator.data)
            and CLOUD_PAD_DIRECTION in self.coordinator.data
        )

    async def async_set_native_value(self, value: float) -> None:
        """Send new pad direction (degrees → DP154)."""
        await self.coordinator.async_set_cloud_setting(pad_direction=round(value))
