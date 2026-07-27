"""Tests for live E15 map image behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.eufy_robomow.const import DP_TASK_ACTIVE
from custom_components.eufy_robomow.coordinator import EufyMowerCoordinator
from custom_components.eufy_robomow.image import EufyRobomowMapImage
from custom_components.eufy_robomow.map import MapSnapshot, Point
from custom_components.eufy_robomow.map_source import LoadedMap, MapSource


def _snapshot(
    *,
    cleaned_paths: tuple[tuple[Point, ...], ...],
    tracking_position: Point,
) -> MapSnapshot:
    return MapSnapshot(
        map_id=539,
        boundary=(Point(0, 0), Point(100, 0), Point(100, 100), Point(0, 100)),
        base_areas=(),
        no_go_areas=(),
        pathways=(),
        cleaned_paths=cleaned_paths,
        mower_position=Point(5, 50),
        tracking_position=tracking_position,
    )


class _SequenceMapSource:
    def __init__(self, snapshots: list[MapSnapshot]) -> None:
        self._snapshots = snapshots
        self._index = 0
        self.streaming_requests: list[bool] = []
        self.status = SimpleNamespace(
            state="healthy",
            last_success=datetime(2026, 7, 27, tzinfo=UTC),
            last_error=None,
        )

    async def async_refresh(self, *, streaming: bool = False) -> LoadedMap:
        self.streaming_requests.append(streaming)
        snapshot = self._snapshots[self._index]
        self._index += 1
        return LoadedMap(
            snapshot_id=f"snapshot-{self._index}",
            captured_at=datetime(2026, 7, 27, 8, self._index, tzinfo=UTC),
            snapshot=snapshot,
        )


def test_map_image_accumulates_live_deltas_and_resets_when_idle(
    tmp_path: Path,
) -> None:
    async def run_test() -> None:
        first = _snapshot(
            cleaned_paths=((Point(10, 10), Point(10, 80)),),
            tracking_position=Point(10, 80),
        )
        delta = _snapshot(
            cleaned_paths=(),
            tracking_position=Point(20, 80),
        )
        source = _SequenceMapSource([first, delta, delta])
        coordinator = SimpleNamespace(data={DP_TASK_ACTIVE: True})
        entry = SimpleNamespace(data={"device_id": "synthetic-device"})
        hass = HomeAssistant(str(tmp_path))
        entity = EufyRobomowMapImage(
            hass,
            cast(EufyMowerCoordinator, coordinator),
            cast(MapSource, source),
            cast(ConfigEntry, entry),
        )

        await entity.async_update()
        assert b'class="cleaned-path"' in (entity.image() or b"")

        await entity.async_update()
        assert b'class="cleaned-path"' in (entity.image() or b"")

        coordinator.data[DP_TASK_ACTIVE] = False
        await entity.async_update()
        assert b'class="cleaned-path"' not in (entity.image() or b"")
        assert source.streaming_requests == [True, True, False]
        await hass.async_stop()

    asyncio.run(run_test())
