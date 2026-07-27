"""Tests for the strict map bundle and source configuration boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
from typing import cast
from zipfile import ZIP_STORED, ZipFile

import aiohttp
import pytest

from homeassistant.core import HomeAssistant

from custom_components.eufy_robomow.map_source import (
    MAP_BUNDLE_CONTENT_TYPE,
    MapSource,
    MapSourceError,
    MapSourceSettings,
    decode_map_bundle,
    read_cached_bundle,
    write_cached_bundle,
)

from .map_fixtures import clean_path_payload, map_payload, point

_DEVICE_ID = "synthetic-device"
_FILENAMES = (
    "map.bin.stream",
    "cleanPath.bin.stream",
    "navPath.bin.stream",
)


def _payloads() -> dict[str, bytes]:
    return {
        "map.bin.stream": map_payload(),
        "cleanPath.bin.stream": clean_path_payload(),
        "navPath.bin.stream": point(11, 12, x_field=2, y_field=3),
    }


def _snapshot_digest(payloads: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for filename in _FILENAMES:
        payload = payloads[filename]
        digest.update(filename.encode("ascii"))
        digest.update(len(payload).to_bytes(8, byteorder="big"))
        digest.update(payload)
    return digest.hexdigest()


def _bundle(
    *,
    device_id: str = _DEVICE_ID,
    mutate_manifest: object | None = None,
    extra_member: bool = False,
) -> bytes:
    payloads = _payloads()
    manifest: object = {
        "schema_version": 1,
        "device_id": device_id,
        "captured_at": 1_700_000_000,
        "snapshot_id": _snapshot_digest(payloads),
        "files": {
            filename: {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for filename, payload in payloads.items()
        },
    }
    if mutate_manifest is not None:
        manifest = mutate_manifest

    output = BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_STORED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, separators=(",", ":")),
        )
        for filename, payload in payloads.items():
            archive.writestr(filename, payload)
        if extra_member:
            archive.writestr("../private", b"not allowed")
    return output.getvalue()


def test_decode_map_bundle_returns_typed_snapshot() -> None:
    loaded = decode_map_bundle(_bundle(), _DEVICE_ID)

    assert loaded.captured_at == datetime.fromtimestamp(1_700_000_000, tz=UTC)
    assert loaded.snapshot.map_id == 539
    assert len(loaded.snapshot.boundary) == 4
    assert MAP_BUNDLE_CONTENT_TYPE == "application/vnd.eufy-robomow-map+zip"


def test_decode_map_bundle_rejects_other_device() -> None:
    with pytest.raises(MapSourceError, match="different device"):
        decode_map_bundle(_bundle(), "other-device")


def test_decode_map_bundle_rejects_unexpected_archive_path() -> None:
    with pytest.raises(MapSourceError, match="unexpected"):
        decode_map_bundle(_bundle(extra_member=True), _DEVICE_ID)


def test_decode_map_bundle_rejects_invalid_manifest() -> None:
    with pytest.raises(MapSourceError, match="manifest"):
        decode_map_bundle(_bundle(mutate_manifest=["not", "an", "object"]), _DEVICE_ID)


def test_cache_round_trip_is_single_atomic_file(tmp_path: Path) -> None:
    cache_file = tmp_path / "private" / "latest.mapbundle"
    encoded = _bundle()

    write_cached_bundle(cache_file, encoded)

    assert read_cached_bundle(cache_file) == encoded
    assert list(cache_file.parent.iterdir()) == [cache_file]


def test_map_source_settings_are_optional() -> None:
    assert (
        MapSourceSettings.from_values(
            base_url="",
            certificate_fingerprint="",
            local_key="0123456789abcdef",
        )
        is None
    )


def test_map_source_settings_derive_authentication() -> None:
    settings = MapSourceSettings.from_values(
        base_url="https://map.example.test/",
        certificate_fingerprint="AA:" * 31 + "AA",
        local_key="0123456789abcdef",
    )

    assert settings is not None
    assert settings.base_url == "https://map.example.test"
    assert len(settings.access_token) == 64
    assert settings.certificate_fingerprint == bytes.fromhex("aa" * 32)


@pytest.mark.parametrize(
    ("url", "fingerprint"),
    [
        ("http://map.example.test", ""),
        ("https://user:secret@map.example.test", ""),
        ("https://map.example.test?device=one", ""),
        ("", "aa" * 32),
        ("https://map.example.test", "invalid"),
    ],
)
def test_map_source_settings_reject_unsafe_values(
    url: str,
    fingerprint: str,
) -> None:
    with pytest.raises(MapSourceError):
        MapSourceSettings.from_values(
            base_url=url,
            certificate_fingerprint=fingerprint,
            local_key="0123456789abcdef",
        )


class _FakeHass:
    async def async_add_executor_job(self, target, *args):
        return target(*args)


class _StaticMapSource(MapSource):
    def __init__(self, *args, response: bytes | BaseException, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._response = response
        self.streaming_requests: list[bool] = []

    async def _async_fetch(self, *, streaming: bool) -> bytes:
        self.streaming_requests.append(streaming)
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response


def _settings() -> MapSourceSettings:
    settings = MapSourceSettings.from_values(
        base_url="https://map.example.test",
        certificate_fingerprint="",
        local_key="0123456789abcdef",
    )
    assert settings is not None
    return settings


def test_map_source_publishes_successful_bundle(tmp_path: Path) -> None:
    cache_file = tmp_path / "latest.mapbundle"
    encoded = _bundle()
    source = _StaticMapSource(
        cast(HomeAssistant, _FakeHass()),
        settings=_settings(),
        device_id=_DEVICE_ID,
        cache_file=cache_file,
        response=encoded,
    )

    loaded = asyncio.run(source.async_refresh())

    assert loaded.snapshot.map_id == 539
    assert source.status.state == "healthy"
    assert read_cached_bundle(cache_file) == encoded


def test_map_source_throttles_idle_and_switches_to_streaming(tmp_path: Path) -> None:
    source = _StaticMapSource(
        cast(HomeAssistant, _FakeHass()),
        settings=_settings(),
        device_id=_DEVICE_ID,
        cache_file=tmp_path / "latest.mapbundle",
        response=_bundle(),
    )

    asyncio.run(source.async_refresh())
    asyncio.run(source.async_refresh())
    asyncio.run(source.async_refresh(streaming=True))
    asyncio.run(source.async_refresh(streaming=True))

    assert source.streaming_requests == [False, True]


def test_map_source_retains_cached_map_after_transport_failure(
    tmp_path: Path,
) -> None:
    cache_file = tmp_path / "latest.mapbundle"
    write_cached_bundle(cache_file, _bundle())
    source = _StaticMapSource(
        cast(HomeAssistant, _FakeHass()),
        settings=_settings(),
        device_id=_DEVICE_ID,
        cache_file=cache_file,
        response=aiohttp.ClientConnectionError("synthetic failure"),
    )

    loaded = asyncio.run(source.async_refresh())

    assert loaded.snapshot.map_id == 539
    assert source.status.state == "stale"
    assert source.status.last_error == "Map source transport failed"
