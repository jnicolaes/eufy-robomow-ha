"""Sensors for Eufy Robomow."""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    DP_BATTERY,
    DP_AREA,
    DP_NETWORK,
    DP_PROGRESS,
    DP_TOTAL_TIME,
    DP_SIGNAL,
    DP_LIVE_VIEW,
    DP_TASK_ACTIVE,
)
from .coordinator import EufyMowerCoordinator

_LOGGER = logging.getLogger(__name__)

# DP125 unit: ~6.6 seconds per unit (confirmed: 36149 units ≈ 66h total)
DP125_SECONDS_PER_UNIT = 6.6

# DPS that should NOT be exposed as generic sensors (already have dedicated entities)
EXCLUDED_DPS = {
    "1",    # Task active      → used internally by lawn_mower entity
    "2",    # Paused           → used internally by lawn_mower entity
    "8",    # Battery          → dedicated Battery sensor
    "26",   # Volume           → dedicated Number entity
    "47",   # Child protection → dedicated Switch entity
    "101",  # Rain detection   → dedicated Switch entity
    "109",  # Signal strength  → dedicated Sensor entity
    "110",  # Cut height       → dedicated Number entity
    "114",  # Live view state  → dedicated Sensor entity
    "113",  # Session telemetry → dedicated EufyMowingProgressSensor + EufySessionDistanceSensor
    "118",  # Return Progress  → dedicated sensor
    "124",  # Coverage         → dedicated EufyCoverageSensor (blob decode)
    "125",  # Total time       → dedicated Total Mow Time sensor
    "126",  # Area             → dedicated Mowed Area sensor
    "132",  # Smart suggestion → dedicated Switch entity
    "133",  # Real lawn map    → dedicated Switch entity
    "134",  # Network          → dedicated Network sensor
    "141",  # Mow yellow grass → dedicated Switch entity
}

# Cloud setting keys that have dedicated entities — exclude from generic sensors
# to avoid duplicating values that are already well-represented.
CLOUD_ENTITY_KEYS = {
    "cloud_path_mm",       # Path distance select (8/10/12 cm)
    "cloud_travel_speed",  # Travel speed select
    "cloud_blade_speed",   # Blade speed select
    "cloud_edge_mm",       # Edge distance number
    "cloud_pad_direction", # Pad direction number
}

# Pre-seed set is now empty — all previously known DPs have dedicated entities.
# Keep the set so the mechanism is ready for future DPs we discover.
PRESEED_DPS: set[str] = set()


@dataclass(frozen=True)
class EufySensorDescription(SensorEntityDescription):
    dp: str = ""


SENSORS: tuple[EufySensorDescription, ...] = (
    EufySensorDescription(
        key="battery",
        dp=DP_BATTERY,
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:battery",
    ),
    EufySensorDescription(
        key="mowed_area",
        dp=DP_AREA,
        name="Mowed Area",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement="units",
        icon="mdi:grass",
    ),
    EufySensorDescription(
        key="total_time",
        dp=DP_TOTAL_TIME,
        name="Total Mow Time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfTime.HOURS,
        icon="mdi:clock-outline",
    ),
    EufySensorDescription(
        key="progress",
        dp=DP_PROGRESS,
        # DP118 meaning:
        #   0       = idle / actively mowing  (not a useful progress value)
        #   1–99    = returning to base       (real progress toward dock)
        #   100     = docked / session done
        # During mowing we return None so HA shows "Unknown" instead of 0%.
        name="Return Progress",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:home-import-outline",
    ),
    EufySensorDescription(
        key="network",
        dp=DP_NETWORK,
        name="Network",
        icon="mdi:wifi",
    ),
    EufySensorDescription(
        key="signal",
        dp=DP_SIGNAL,
        name="Signal Strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="dBm",
        icon="mdi:signal",
    ),
    EufySensorDescription(
        key="live_view",
        dp=DP_LIVE_VIEW,
        name="Live View State",
        icon="mdi:camera",
    ),
)

# ── Shared protobuf helpers ────────────────────────────────────────────────────

def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a protobuf varint from *data* at *pos*. Returns (value, new_pos)."""
    result = shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _proto_flat(data: bytes) -> dict[int, Any]:
    """Decode a protobuf blob into a flat {field_num: value} dict.

    Wire-type 2 (length-delimited) values are returned as raw bytes so the
    caller can recurse into nested messages if needed.  Repeated fields keep
    only the last value (sufficient for our use-cases).
    """
    fields: dict[int, Any] = {}
    pos = 0
    while pos < len(data):
        try:
            tag, pos = _read_varint(data, pos)
            fn = tag >> 3
            wt = tag & 7
            if wt == 0:
                val, pos = _read_varint(data, pos)
                fields[fn] = val
            elif wt == 2:
                length, pos = _read_varint(data, pos)
                fields[fn] = data[pos : pos + length]
                pos += length
            elif wt == 1:
                pos += 8   # fixed64 — skip
            elif wt == 5:
                pos += 4   # fixed32 / float — skip
            else:
                break      # unknown wire type — bail out
        except Exception:
            break
    return fields


# ── DP113 live session telemetry blob ─────────────────────────────────────────
# DP113 is updated every ~60–90 s while mowing.  Confirmed field meanings:
#   field1 = mode/constant (4 while mowing)
#   field2 = zone total planned area (units TBD; 913 for zone 1, 577 for zone 2)
#   field3 = area covered this session so far (same units, incrementing)
#   field5 = distance traveled this session (metres)
#
# field3 / field2 × 100 = real-time mowing completion %.
# When field2 is absent (session not started yet) all values are None.
_DP_SESSION = "113"

_Dp113Result = tuple[int | None, int | None, int | None]


def _decode_dp113(raw_blob: str | None) -> _Dp113Result:
    """Return (zone_total_area, session_area_covered, session_distance_m).

    All three are None when the blob is absent or the session has not started.
    """
    if not raw_blob or not isinstance(raw_blob, str):
        return None, None, None
    try:
        data = base64.b64decode(raw_blob)
    except Exception:
        return None, None, None

    f = _proto_flat(data)
    total   = f.get(2)   # zone planned area
    covered = f.get(3)   # area covered this session
    dist    = f.get(5)   # distance in metres

    # field2 must be a positive int for data to be meaningful
    if not isinstance(total, int) or total <= 0:
        return None, None, None

    return (
        total,
        covered if isinstance(covered, int) else None,
        dist    if isinstance(dist,    int) else None,
    )


# ── DP124 cloud blob: area/coverage stats ─────────────────────────────────────
# DP124 is a protobuf blob fetched from the cloud every 5 minutes.
#   field1 = total lawn area (raw units — scale TBD, likely ×0.01 m²)
#   field2 = area covered in last session (same unit)
#   field3 = map coverage score (increments ≈ +1 per mowing session; lifetime score)
# We expose field3 as a "Map Coverage" sensor.
_DP_COVERAGE = "124"


def _decode_dp124_field3(raw_blob: str | None) -> int | None:
    """Decode DP124 protobuf blob and return field3 (map coverage score)."""
    if not raw_blob or not isinstance(raw_blob, str):
        return None
    try:
        data = base64.b64decode(raw_blob)
    except Exception:
        return None
    return _proto_flat(data).get(3)  # type: ignore[return-value]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: EufyMowerCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Add dedicated sensors (standard + session progress + coverage)
    entities: list = [EufySensor(coordinator, entry, desc) for desc in SENSORS]
    entities.append(EufyMowingProgressSensor(coordinator, entry))
    entities.append(EufySessionDistanceSensor(coordinator, entry))
    entities.append(EufyCoverageSensor(coordinator, entry))
    async_add_entities(entities)

    # Track which DP IDs already have a generic sensor so we never duplicate.
    # Seed with the DPs that have dedicated entities.
    dp_with_sensor: set[str] = set(EXCLUDED_DPS) | CLOUD_ENTITY_KEYS

    def _add_generic_sensors_for_new_dps(current_dps: dict | None = None) -> None:
        """Create EufyGenericSensor for any DP not yet covered by a sensor.

        *current_dps* is the freshly-polled dict passed by coordinator callbacks.
        Falls back to coordinator.data when called at setup time (no arg).
        """
        data = current_dps if current_dps is not None else coordinator.data
        if not data:
            return
        new_entities = []
        for dp_id in data:
            if dp_id not in dp_with_sensor:
                dp_with_sensor.add(dp_id)
                new_entities.append(EufyGenericSensor(coordinator, entry, dp_id))
        if new_entities:
            _LOGGER.debug(
                "Adding generic sensors for new DPs: %s",
                [e.name for e in new_entities],
            )
            async_add_entities(new_entities)

    # Pre-seed sensors for known DPs that may not be present in every poll.
    # Currently empty — all known DPs have dedicated entities.
    preseed_entities = []
    for dp_id in PRESEED_DPS:
        if dp_id not in dp_with_sensor:
            dp_with_sensor.add(dp_id)
            preseed_entities.append(EufyGenericSensor(coordinator, entry, dp_id))
    if preseed_entities:
        async_add_entities(preseed_entities)

    # Create sensors for all DPS already known at setup time (first refresh
    # includes both local + cloud DPS, so this catches everything in one go).
    _add_generic_sensors_for_new_dps()

    # Register with coordinator: whenever a truly new DP key appears in any future
    # poll (local or cloud), a sensor is added on-the-fly.
    coordinator.async_add_new_dp_listener(_add_generic_sensors_for_new_dps)


def _decode_blob(value: Any) -> str:
    """Attempt to decode blob/string values for human-readable output."""
    if isinstance(value, str):
        try:
            # Try base64 decode first (common for protobuf blobs)
            decoded = base64.b64decode(value)
            # Try to detect if it's ASCII text
            try:
                text = decoded.decode("utf-8")
                if all(32 <= ord(c) < 127 or c in "\n\r\t" for c in text):
                    return f"<text> {text}"
            except UnicodeDecodeError:
                pass

            # Return hex representation for binary data
            return f"<blob ({len(decoded)} bytes)> {decoded.hex()}"
        except (binascii.Error, ValueError):
            # Not base64, return as-is
            pass

    return str(value)


class EufySensor(CoordinatorEntity[EufyMowerCoordinator], SensorEntity):
    """A sensor that reads one DPS value."""

    entity_description: EufySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EufyMowerCoordinator,
        entry: ConfigEntry,
        description: EufySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_DEVICE_ID])},
        )

    @property
    def native_value(self) -> Any:
        raw = self.coordinator.data.get(self.entity_description.dp)
        if raw is None:
            return None
        # Convert DP125 raw units → hours
        if self.entity_description.dp == DP_TOTAL_TIME:
            return round((raw * DP125_SECONDS_PER_UNIT) / 3600, 1)
        # Convert DP109 raw signal → negative dBm (device sends 58, means -58 dBm)
        if self.entity_description.dp == DP_SIGNAL:
            return -raw
        # DP118 "Return Progress":
        #   0 = idle / mowing — not a meaningful progress value, return None
        #       so HA shows "Unknown" rather than a misleading 0%.
        #   1–99 = returning to base (real % of journey back to dock)
        #   100  = docked / fully done
        if self.entity_description.dp == DP_PROGRESS:
            if raw == 0:
                # Show None only when a task is actually running (mowing).
                # When truly idle (DP1=False) leave as 0 so the sensor has a value.
                dp1 = self.coordinator.data.get(DP_TASK_ACTIVE, False)
                if dp1:
                    return None  # actively mowing — no return progress yet
            return raw
        return raw


class EufyCoverageSensor(CoordinatorEntity[EufyMowerCoordinator], SensorEntity):
    """Reads the DP124 protobuf blob and exposes field3 as map coverage %.

    DP124 is a cloud-side blob fetched every 5 minutes.  Field3 appears to
    track how much of the lawn has been mapped / visited (0–100 scale).
    The exact semantics are still being reverse-engineered.
    """

    _attr_has_entity_name = True
    _attr_name = "Map Coverage"
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:map-check"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: EufyMowerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_map_coverage"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_DEVICE_ID])},
        )

    @property
    def native_value(self) -> int | None:
        raw_blob = self.coordinator.data.get(_DP_COVERAGE)
        return _decode_dp124_field3(raw_blob)


class EufyMowingProgressSensor(CoordinatorEntity[EufyMowerCoordinator], SensorEntity):
    """Real mowing completion % derived from DP113 session telemetry blob.

    Computes field3 (area covered this session) / field2 (zone total planned area) × 100.
    Returns None when not actively mowing (blob absent or session not started).
    """

    _attr_has_entity_name = True
    _attr_name = "Mowing Progress"
    _attr_icon = "mdi:percent"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: EufyMowerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_mowing_progress"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_DEVICE_ID])},
        )

    @property
    def native_value(self) -> float | None:
        raw_blob = self.coordinator.data.get(_DP_SESSION)
        total, covered, _ = _decode_dp113(raw_blob)
        if total is None or covered is None:
            return None
        return round(covered / total * 100, 1)


class EufySessionDistanceSensor(CoordinatorEntity[EufyMowerCoordinator], SensorEntity):
    """Distance traveled in the current mowing session (metres), from DP113 field5.

    Returns None when no active session blob is available.
    """

    _attr_has_entity_name = True
    _attr_name = "Session Distance"
    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: EufyMowerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_session_distance"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_DEVICE_ID])},
        )

    @property
    def native_value(self) -> int | None:
        raw_blob = self.coordinator.data.get(_DP_SESSION)
        _, _, dist = _decode_dp113(raw_blob)
        return dist


class EufyGenericSensor(CoordinatorEntity[EufyMowerCoordinator], SensorEntity):
    """A generic sensor that exposes any DPS value."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: EufyMowerCoordinator,
        entry: ConfigEntry,
        dp_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._dp_id = dp_id
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_generic_{dp_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_DEVICE_ID])},
        )
        self._attr_name = f"DP{dp_id}"
        self._attr_native_value = None

    @property
    def native_value(self) -> Any:
        raw = self.coordinator.data.get(self._dp_id)
        if raw is None:
            return None

        # Decode blob/string values for human readability
        return _decode_blob(raw)

    async def async_added_to_hass(self) -> None:
        """Register for coordinator updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_update))

    def _handle_update(self) -> None:
        """Handle coordinator update."""
        self.async_write_ha_state()
