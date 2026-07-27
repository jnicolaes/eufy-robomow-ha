"""Validated HTTPS source and latest-good cache for Eufy E15 maps."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from io import BytesIO
import json
import os
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlsplit
from zipfile import ZIP_STORED, BadZipFile, ZipFile, ZipInfo

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .map import MapDecodeError, MapSnapshot, parse_map_snapshot

MAP_BUNDLE_CONTENT_TYPE = "application/vnd.eufy-robomow-map+zip"
MAP_CACHE_DIRECTORY = "eufy_robomow_maps"
MAP_REFRESH_INTERVAL = timedelta(minutes=5)
MAP_STREAM_REFRESH_INTERVAL = timedelta(seconds=2)
MAP_MODE_HEADER = "X-Eufy-Map-Mode"
MAP_MODE_IDLE = "idle"
MAP_MODE_STREAM = "stream"

_MAP_FILENAME = "map.bin.stream"
_CLEAN_PATH_FILENAME = "cleanPath.bin.stream"
_NAV_PATH_FILENAME = "navPath.bin.stream"
_MAP_FILENAMES = (
    _MAP_FILENAME,
    _CLEAN_PATH_FILENAME,
    _NAV_PATH_FILENAME,
)
_MANIFEST_FILENAME = "manifest.json"
_EXPECTED_MEMBERS = frozenset((*_MAP_FILENAMES, _MANIFEST_FILENAME))
_MAX_MAP_FILE_SIZE = 5 * 1024 * 1024
_MAX_BUNDLE_SIZE = 16 * 1024 * 1024
_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_CONTEXT = b"eufy-robomow-map-helper-v1"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=5)


class MapSourceError(ValueError):
    """Raised when a map source or bundle cannot be consumed safely."""


@dataclass(frozen=True, slots=True)
class MapSourceSettings:
    """Validated optional map-source connection settings."""

    base_url: str
    access_token: str
    certificate_fingerprint: bytes | None

    @classmethod
    def from_values(
        cls,
        *,
        base_url: str,
        certificate_fingerprint: str,
        local_key: str,
    ) -> MapSourceSettings | None:
        """Validate options, returning ``None`` when map support is disabled."""
        normalized_url = base_url.strip().rstrip("/")
        normalized_fingerprint = (
            certificate_fingerprint.lower().replace(":", "").replace(" ", "")
        )
        if not normalized_url:
            if normalized_fingerprint:
                raise MapSourceError(
                    "Map certificate fingerprint requires a map source URL"
                )
            return None

        parsed = urlsplit(normalized_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise MapSourceError(
                "Map source must be an HTTPS base URL without credentials, "
                "query, or fragment"
            )
        if normalized_fingerprint and not _SHA256_PATTERN.fullmatch(
            normalized_fingerprint
        ):
            raise MapSourceError(
                "Map certificate fingerprint must be 64 hexadecimal characters"
            )

        encoded_key = local_key.encode()
        if len(encoded_key) not in (16, 32):
            raise MapSourceError("Mower local key must encode to 16 or 32 bytes")
        access_token = hmac.new(
            encoded_key,
            _TOKEN_CONTEXT,
            hashlib.sha256,
        ).hexdigest()
        return cls(
            base_url=normalized_url,
            access_token=access_token,
            certificate_fingerprint=(
                bytes.fromhex(normalized_fingerprint)
                if normalized_fingerprint
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class LoadedMap:
    """One complete, validated E15 map snapshot."""

    snapshot_id: str
    captured_at: datetime
    snapshot: MapSnapshot


@dataclass(frozen=True, slots=True)
class MapSourceStatus:
    """Secret-safe operational status for the image entity."""

    state: str
    last_success: datetime | None
    last_error: str | None


class MapSource:
    """Fetch a map bundle and retain the latest valid snapshot."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        settings: MapSourceSettings,
        device_id: str,
        cache_file: Path,
    ) -> None:
        self._hass = hass
        self._settings = settings
        self._device_id = device_id
        self._cache_file = cache_file
        self._current: LoadedMap | None = None
        self._last_error: str | None = None
        self._last_attempt: float | None = None
        self._consecutive_failures = 0
        self._streaming = False
        self._etag: str | None = None

    @property
    def status(self) -> MapSourceStatus:
        """Return acquisition health without exposing connection details."""
        if self._current is None:
            state = "error" if self._last_error else "starting"
        elif self._last_error:
            state = "stale"
        else:
            state = "healthy"
        return MapSourceStatus(
            state=state,
            last_success=(
                self._current.captured_at if self._current is not None else None
            ),
            last_error=self._last_error,
        )

    async def async_refresh(self, *, streaming: bool = False) -> LoadedMap:
        """Fetch the latest bundle, falling back to the cached valid map."""
        now = time.monotonic()
        mode_changed = streaming != self._streaming
        self._streaming = streaming
        if (
            not mode_changed
            and self._current is not None
            and self._last_attempt is not None
            and now - self._last_attempt < self._next_refresh_delay(streaming)
        ):
            return self._current
        self._last_attempt = now

        try:
            encoded = await self._async_fetch(streaming=streaming)
            if encoded is None:
                if self._current is None:
                    raise MapSourceError("Map source returned no initial snapshot")
                self._last_error = None
                self._consecutive_failures = 0
                return self._current
            loaded = await self._hass.async_add_executor_job(
                decode_map_bundle,
                encoded,
                self._device_id,
            )
            _reject_future_snapshot(loaded)
            if self._current is None or loaded.snapshot_id != self._current.snapshot_id:
                await self._hass.async_add_executor_job(
                    write_cached_bundle,
                    self._cache_file,
                    encoded,
                )
            self._current = loaded
            self._last_error = None
            self._consecutive_failures = 0
            return loaded
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            MapDecodeError,
            MapSourceError,
            OSError,
        ) as exc:
            self._consecutive_failures += 1
            self._last_error = _safe_error_message(exc)

        if self._current is not None:
            return self._current

        try:
            encoded = await self._hass.async_add_executor_job(
                read_cached_bundle,
                self._cache_file,
            )
            self._current = await self._hass.async_add_executor_job(
                decode_map_bundle,
                encoded,
                self._device_id,
            )
            _reject_future_snapshot(self._current)
        except (MapDecodeError, MapSourceError, OSError) as exc:
            raise MapSourceError(
                "No valid E15 map is available from the source or cache"
            ) from exc
        return self._current

    def _next_refresh_delay(self, streaming: bool) -> float:
        interval = (
            MAP_STREAM_REFRESH_INTERVAL if streaming else MAP_REFRESH_INTERVAL
        ).total_seconds()
        if not self._consecutive_failures:
            return interval
        return max(interval, min(2**self._consecutive_failures, 60))

    async def _async_fetch(self, *, streaming: bool) -> bytes | None:
        session = async_get_clientsession(self._hass)
        ssl: bool | aiohttp.Fingerprint = True
        if self._settings.certificate_fingerprint is not None:
            ssl = aiohttp.Fingerprint(self._settings.certificate_fingerprint)

        headers = {
            "Authorization": f"Bearer {self._settings.access_token}",
            "Accept": MAP_BUNDLE_CONTENT_TYPE,
            MAP_MODE_HEADER: MAP_MODE_STREAM if streaming else MAP_MODE_IDLE,
        }
        if self._etag is not None:
            headers["If-None-Match"] = self._etag
        async with session.get(
            f"{self._settings.base_url}/v1/map",
            headers=headers,
            ssl=ssl,
            timeout=_REQUEST_TIMEOUT,
        ) as response:
            if response.status == 304:
                return None
            if response.status == 401:
                raise MapSourceError("Map source rejected authentication")
            if response.status == 503:
                raise MapSourceError("Map source has no complete snapshot")
            if response.status != 200:
                raise MapSourceError(f"Map source returned HTTP {response.status}")

            content_type = response.headers.get("Content-Type", "").split(
                ";",
                maxsplit=1,
            )[0]
            if content_type != MAP_BUNDLE_CONTENT_TYPE:
                raise MapSourceError("Map source returned an unexpected content type")
            if (
                response.content_length is not None
                and response.content_length > _MAX_BUNDLE_SIZE
            ):
                raise MapSourceError("Map source response exceeds 16 MiB")

            encoded = await response.content.read(_MAX_BUNDLE_SIZE + 1)
            if len(encoded) > _MAX_BUNDLE_SIZE:
                raise MapSourceError("Map source response exceeds 16 MiB")
            etag = response.headers.get("ETag")
            if etag and len(etag) <= 256 and "\n" not in etag and "\r" not in etag:
                self._etag = etag
            return encoded


def decode_map_bundle(data: bytes, expected_device_id: str) -> LoadedMap:
    """Validate and decode an untrusted map bundle."""
    if not data:
        raise MapSourceError("Map bundle is empty")
    if len(data) > _MAX_BUNDLE_SIZE:
        raise MapSourceError("Map bundle exceeds 16 MiB")

    try:
        with ZipFile(BytesIO(data), mode="r") as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(names) != len(set(names)):
                raise MapSourceError("Map bundle contains duplicate members")
            if frozenset(names) != _EXPECTED_MEMBERS:
                raise MapSourceError("Map bundle contains unexpected or missing files")
            for member in members:
                _validate_member(member)

            manifest = _decode_manifest(archive.read(_MANIFEST_FILENAME))
            payloads = {filename: archive.read(filename) for filename in _MAP_FILENAMES}
    except BadZipFile as exc:
        raise MapSourceError("Map bundle is not a valid ZIP archive") from exc

    if manifest.get("schema_version") != _SCHEMA_VERSION:
        raise MapSourceError("Map bundle uses an unsupported schema version")
    device_id = manifest.get("device_id")
    if device_id != expected_device_id:
        raise MapSourceError("Map bundle belongs to a different device")

    captured_at_raw = manifest.get("captured_at")
    if not isinstance(captured_at_raw, int) or isinstance(captured_at_raw, bool):
        raise MapSourceError("Map bundle captured_at must be an integer")
    try:
        captured_at = datetime.fromtimestamp(captured_at_raw, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise MapSourceError("Map bundle captured_at is invalid") from exc

    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(_MAP_FILENAMES):
        raise MapSourceError("Map bundle file inventory is invalid")
    for filename, payload in payloads.items():
        if not payload or len(payload) > _MAX_MAP_FILE_SIZE:
            raise MapSourceError(f"Map payload {filename} has an invalid size")
        metadata = files.get(filename)
        if not isinstance(metadata, dict):
            raise MapSourceError(f"Map metadata for {filename} is invalid")
        expected_hash = metadata.get("sha256")
        if (
            metadata.get("size") != len(payload)
            or not isinstance(expected_hash, str)
            or not _SHA256_PATTERN.fullmatch(expected_hash)
            or hashlib.sha256(payload).hexdigest() != expected_hash
        ):
            raise MapSourceError(f"Map payload {filename} does not match its manifest")

    snapshot_id = manifest.get("snapshot_id")
    if (
        not isinstance(snapshot_id, str)
        or not _SHA256_PATTERN.fullmatch(snapshot_id)
        or snapshot_id != _snapshot_digest(payloads)
    ):
        raise MapSourceError("Map bundle snapshot identifier is invalid")

    return LoadedMap(
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        snapshot=parse_map_snapshot(
            payloads[_MAP_FILENAME],
            payloads[_CLEAN_PATH_FILENAME],
            payloads[_NAV_PATH_FILENAME],
        ),
    )


def read_cached_bundle(path: Path) -> bytes:
    """Read one bounded latest-good bundle from disk."""
    metadata = path.stat()
    if not path.is_file() or metadata.st_size <= 0:
        raise MapSourceError("Cached map bundle is empty or not a file")
    if metadata.st_size > _MAX_BUNDLE_SIZE:
        raise MapSourceError("Cached map bundle exceeds 16 MiB")
    return path.read_bytes()


def write_cached_bundle(path: Path, data: bytes) -> None:
    """Atomically replace the single latest-good bundle."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_member(member: ZipInfo) -> None:
    if member.filename not in _EXPECTED_MEMBERS:
        raise MapSourceError("Map bundle contains an unsupported path")
    if member.is_dir() or member.flag_bits & 0x1:
        raise MapSourceError("Map bundle contains a directory or encrypted member")
    if member.compress_type != ZIP_STORED:
        raise MapSourceError("Map bundle members must be stored without compression")
    maximum = 64 * 1024 if member.filename == _MANIFEST_FILENAME else _MAX_MAP_FILE_SIZE
    if member.file_size <= 0 or member.file_size > maximum:
        raise MapSourceError(f"Map bundle member {member.filename} has an invalid size")


def _decode_manifest(data: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MapSourceError("Map bundle manifest is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise MapSourceError("Map bundle manifest must be an object")
    return decoded


def _snapshot_digest(payloads: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for filename in _MAP_FILENAMES:
        payload = payloads[filename]
        digest.update(filename.encode("ascii"))
        digest.update(len(payload).to_bytes(8, byteorder="big"))
        digest.update(payload)
    return digest.hexdigest()


def _safe_error_message(exc: BaseException) -> str:
    if isinstance(exc, aiohttp.ServerFingerprintMismatch):
        return "Map source certificate fingerprint does not match"
    if isinstance(exc, aiohttp.ClientConnectorCertificateError):
        return "Map source TLS certificate validation failed"
    if isinstance(exc, aiohttp.ClientConnectorError):
        return "Map source connection failed"
    if isinstance(exc, asyncio.TimeoutError):
        return "Map source request timed out"
    if isinstance(exc, MapSourceError):
        return str(exc)[:240]
    if isinstance(exc, MapDecodeError):
        return "Map source returned malformed E15 data"
    if isinstance(exc, OSError):
        return "Map cache I/O failed"
    if isinstance(exc, aiohttp.ClientError):
        return "Map source transport failed"
    return "Map acquisition failed"


def _reject_future_snapshot(loaded: LoadedMap) -> None:
    if loaded.captured_at > datetime.now(tz=UTC) + timedelta(minutes=5):
        raise MapSourceError("Map source returned a future-dated snapshot")
