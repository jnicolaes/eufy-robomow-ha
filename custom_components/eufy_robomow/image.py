"""Optional read-only E15 map image."""

from __future__ import annotations

from functools import partial
import logging
from pathlib import Path

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_MAP_CERTIFICATE_FINGERPRINT,
    CONF_MAP_SOURCE_URL,
    DOMAIN,
    DP_TASK_ACTIVE,
)
from .coordinator import EufyMowerCoordinator
from .map import MapSnapshot, merge_live_snapshots
from .map_renderer import MAP_CONTENT_TYPE, render_map_svg
from .map_source import (
    MAP_CACHE_DIRECTORY,
    MAP_STREAM_REFRESH_INTERVAL,
    MapSource,
    MapSourceError,
    MapSourceSettings,
)

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = MAP_STREAM_REFRESH_INTERVAL


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the map only when an optional source is configured."""
    settings = MapSourceSettings.from_values(
        base_url=entry.options.get(CONF_MAP_SOURCE_URL, ""),
        certificate_fingerprint=entry.options.get(
            CONF_MAP_CERTIFICATE_FINGERPRINT,
            "",
        ),
        local_key=entry.data[CONF_LOCAL_KEY],
    )
    if settings is None:
        return

    coordinator: EufyMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    cache_root = Path(hass.config.path(MAP_CACHE_DIRECTORY))
    cache_file = cache_root / entry.entry_id / "latest.mapbundle"
    async_add_entities(
        [
            EufyRobomowMapImage(
                hass,
                coordinator,
                MapSource(
                    hass,
                    settings=settings,
                    device_id=entry.data[CONF_DEVICE_ID],
                    cache_file=cache_file,
                    cache_root=cache_root,
                ),
                entry,
            )
        ],
        update_before_add=True,
    )


class EufyRobomowMapImage(ImageEntity):
    """Expose the latest validated E15 map as an SVG image."""

    _attr_has_entity_name = True
    _attr_translation_key = "map"
    _attr_icon = "mdi:map"
    _attr_content_type = MAP_CONTENT_TYPE

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EufyMowerCoordinator,
        source: MapSource,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(hass)
        self._hass = hass
        self._coordinator = coordinator
        self._source = source
        self._content: bytes | None = None
        self._render_key: tuple[str, bool] | None = None
        self._live_snapshot: MapSnapshot | None = None
        self._attr_unique_id = f"{entry.data[CONF_DEVICE_ID]}_map"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_DEVICE_ID])},
            name="Eufy Robomow E15",
            manufacturer="Eufy (Anker)",
            model="E15",
        )

    @property
    def available(self) -> bool:
        """Return whether a valid current or cached map is available."""
        return self._content is not None

    def image(self) -> bytes | None:
        """Return the current rendered map."""
        return self._content

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose bounded, secret-safe acquisition health."""
        status = self._source.status
        return {
            "acquisition_status": status.state,
            "acquisition_last_success": status.last_success,
            "acquisition_last_error": status.last_error,
        }

    async def async_update(self) -> None:
        """Fetch and render the latest valid map."""
        include_cleaned_paths = bool(self._coordinator.data.get(DP_TASK_ACTIVE, False))
        try:
            loaded = await self._source.async_refresh(
                streaming=include_cleaned_paths,
            )
        except MapSourceError as exc:
            _LOGGER.debug("E15 map is not available yet: %s", exc)
            return

        if include_cleaned_paths:
            snapshot = (
                merge_live_snapshots(self._live_snapshot, loaded.snapshot)
                if self._live_snapshot is not None
                else loaded.snapshot
            )
            self._live_snapshot = snapshot
        else:
            snapshot = loaded.snapshot
            self._live_snapshot = None

        render_key = (loaded.snapshot_id, include_cleaned_paths)
        if render_key == self._render_key:
            return

        self._content = await self._hass.async_add_executor_job(
            partial(
                render_map_svg,
                snapshot,
                include_cleaned_paths=include_cleaned_paths,
            )
        )
        self._render_key = render_key
        self._attr_image_last_updated = loaded.captured_at
        self.async_update_token()
