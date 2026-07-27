"""Tests for the strict map bundle and source configuration boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
from stat import S_IMODE
from typing import cast
from unittest.mock import patch
from zipfile import ZIP_STORED, ZipFile

import aiohttp
import pytest

from homeassistant.core import HomeAssistant

from custom_components.eufy_robomow.map_source import (
    MAP_BUNDLE_CONTENT_TYPE,
    MapSource,
    MapSourceError,
    MapSourceSettings,
    _MapFetch,
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
    captured_at: int = 1_700_000_000,
    mutate_manifest: object | None = None,
    extra_member: bool = False,
) -> bytes:
    payloads = _payloads()
    manifest: object = {
        "schema_version": 1,
        "device_id": device_id,
        "captured_at": captured_at,
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
    cache_root = tmp_path / "eufy_robomow_maps"
    cache_root.mkdir(mode=0o777)
    cache_root.chmod(0o777)
    cache_file = cache_root / "entry" / "latest.mapbundle"
    encoded = _bundle()

    write_cached_bundle(cache_file, encoded, cache_root)

    assert read_cached_bundle(cache_file) == encoded
    assert list(cache_file.parent.iterdir()) == [cache_file]
    assert S_IMODE(cache_root.stat().st_mode) == 0o700
    assert S_IMODE(cache_file.parent.stat().st_mode) == 0o700
    assert S_IMODE(cache_file.stat().st_mode) == 0o600


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
    def __init__(
        self,
        *args,
        response: bytes | BaseException,
        response_etag: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._response = response
        self._response_etag = response_etag
        self.streaming_requests: list[bool] = []

    async def _async_fetch(self, *, streaming: bool) -> _MapFetch:
        self.streaming_requests.append(streaming)
        if isinstance(self._response, BaseException):
            raise self._response
        return _MapFetch(self._response, self._response_etag)


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


def test_map_source_backs_off_before_first_success(tmp_path: Path) -> None:
    source = _StaticMapSource(
        cast(HomeAssistant, _FakeHass()),
        settings=_settings(),
        device_id=_DEVICE_ID,
        cache_file=tmp_path / "latest.mapbundle",
        response=aiohttp.ClientConnectionError("synthetic failure"),
    )

    with pytest.raises(MapSourceError, match="No valid"):
        asyncio.run(source.async_refresh(streaming=True))
    with pytest.raises(MapSourceError, match="transport failed"):
        asyncio.run(source.async_refresh(streaming=True))

    assert source.streaming_requests == [True]


def test_map_source_rejects_older_snapshot(tmp_path: Path) -> None:
    source = _StaticMapSource(
        cast(HomeAssistant, _FakeHass()),
        settings=_settings(),
        device_id=_DEVICE_ID,
        cache_file=tmp_path / "latest.mapbundle",
        response=_bundle(captured_at=1_700_000_001),
        response_etag='"new"',
    )

    current = asyncio.run(source.async_refresh())
    source._response = _bundle(captured_at=1_700_000_000)
    source._response_etag = '"old"'
    retained = asyncio.run(source.async_refresh(streaming=True))

    assert retained is current
    assert retained.captured_at == datetime.fromtimestamp(1_700_000_001, tz=UTC)
    assert source.status.state == "stale"
    assert source.status.last_error == "Map source returned an older snapshot"
    assert source._etags == {False: '"new"'}


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


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int,
        body: bytes = b"",
        etag: str | None = None,
    ) -> None:
        self.status = status
        self.headers = {
            "Content-Type": MAP_BUNDLE_CONTENT_TYPE,
            **({"ETag": etag} if etag is not None else {}),
        }
        self.content_length = len(body)
        self.content = self
        self._body = body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def read(self, size: int) -> bytes:
        return self._body


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.request_headers: list[dict[str, str]] = []

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        headers = cast(dict[str, str], kwargs["headers"])
        self.request_headers.append(dict(headers))
        return self._responses.pop(0)


def test_map_source_tracks_etags_per_mode(tmp_path: Path) -> None:
    session = _FakeSession(
        [
            _FakeResponse(status=200, body=_bundle(), etag='"idle"'),
            _FakeResponse(status=200, body=_bundle(), etag='"stream"'),
            _FakeResponse(status=304),
            _FakeResponse(status=304),
        ]
    )
    source = MapSource(
        cast(HomeAssistant, _FakeHass()),
        settings=_settings(),
        device_id=_DEVICE_ID,
        cache_file=tmp_path / "latest.mapbundle",
    )

    with patch(
        "custom_components.eufy_robomow.map_source.async_get_clientsession",
        return_value=session,
    ):
        idle = asyncio.run(source._async_fetch(streaming=False))
        stream = asyncio.run(source._async_fetch(streaming=True))
        source._etags[False] = idle.etag or ""
        source._etags[True] = stream.etag or ""
        asyncio.run(source._async_fetch(streaming=False))
        asyncio.run(source._async_fetch(streaming=True))

    assert [
        headers.get("If-None-Match") for headers in session.request_headers
    ] == [None, None, '"idle"', '"stream"']
