"""Eufy cloud settings client.

Authenticates via Eufy/Tuya mobile API (same flow as eufy-clean-local-key-grabber)
and reads/writes DP155, a base64-encoded protobuf blob that stores the five
cloud-managed settings for the E15:

    field 1  (message) : const sub-msg {field1: 40}   ← sub-message, NOT bare varint
    field 2  (message) : travel speed  — empty = slow, {1:1} = normal, {1:2} = fast
    field 3  (message) : edge distance — {1: mm}  (signed; negative = beyond wire)
    field 4  (message) : pad direction — {f2:{f1: angle}, f3: fixed, f5: fixed}
                         angle in degrees (1 unit = 1°); 0 = west (9 o'clock);
                         90 = north (12 o'clock); 180 = east (3 o'clock), etc.
    field 5  (message) : path distance — {1: mm}
    field 6  (message) : blade speed   — empty = slow, {1:1} = normal, {1:2} = fast
    field 7  (varint)  : mirrors path_mm (same value as field 5's inner varint)

NOTE: DP154 was previously assumed to hold pad direction but testing showed it does
NOT change when the app's direction dial is moved.  DP155 field 4 is the correct
location.  DP154's purpose is currently unknown.

All API calls are synchronous; callers must run them in an executor thread
(hass.async_add_executor_job) to avoid blocking the event loop.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac_module
import json
import logging
import math
import random
import string
import time
import uuid
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)

# ── App credentials (extracted from Eufy Home Android app) ────────────────────

_EUFY_BASE_URL = "https://home-api.eufylife.com/v1/"
_EUFY_CLIENT_ID = "eufyhome-app"
_EUFY_CLIENT_SECRET = "GQCpr9dSp3uQpsOMgJ4xQ"
_EUFY_PLATFORM = "sdk_gphone64_arm64"
_EUFY_LANGUAGE = "en"
_EUFY_TIMEZONE = "Europe/London"

_TUYA_CLIENT_ID = "yx5v9uc3ef9wg3v9atje"
_TUYA_INITIAL_URL = "https://a1.tuyaeu.com"
_TUYA_APP_SECRET = "s8x78u7xwymasd9kqa7a73pjhxqsedaj"
_TUYA_BMP_SECRET = "cepev5pfnhua4dkqkdpmnrdxx378mpjr"
_TUYA_HMAC_KEY = f"A_{_TUYA_BMP_SECRET}_{_TUYA_APP_SECRET}".encode("utf-8")

# AES-128-CBC key+IV for deriving the Tuya login password from the UID
_TUYA_PASSWORD_KEY = bytes(
    [36, 78, 109, 138, 86, 172, 135, 145, 36, 67, 45, 139, 108, 188, 162, 196]
)
_TUYA_PASSWORD_IV = bytes(
    [119, 36, 86, 242, 167, 102, 76, 243, 57, 44, 53, 151, 233, 62, 87, 71]
)

# Query parameters included in the HMAC signature
_SIGNATURE_PARAMS = {
    "a",
    "v",
    "lat",
    "lon",
    "lang",
    "deviceId",
    "appVersion",
    "ttid",
    "isH5",
    "h5Token",
    "os",
    "clientId",
    "postData",
    "time",
    "requestId",
    "et",
    "n4h5",
    "sid",
    "sp",
}

_DEFAULT_TUYA_PARAMS: dict[str, str] = {
    "appVersion": "2.4.0",
    "deviceId": "",
    "platform": _EUFY_PLATFORM,
    "clientId": _TUYA_CLIENT_ID,
    "lang": _EUFY_LANGUAGE,
    "osSystem": "12",
    "os": "Android",
    "timeZoneId": _EUFY_TIMEZONE,
    "ttid": "android",
    "et": "0.0.1",
    "sdkVersion": "3.0.8cAnker",
}

# ── Speed option values ────────────────────────────────────────────────────────

SPEED_SLOW = "slow"
SPEED_NORMAL = "normal"
SPEED_FAST = "fast"
SPEED_OPTIONS = [SPEED_SLOW, SPEED_NORMAL, SPEED_FAST]

_SPEED_TO_INT: dict[str, int] = {SPEED_SLOW: 0, SPEED_NORMAL: 1, SPEED_FAST: 2}
_INT_TO_SPEED: dict[int, str] = {0: SPEED_SLOW, 1: SPEED_NORMAL, 2: SPEED_FAST}

# ── DP155 preserved constants ──────────────────────────────────────────────────

_FIELD1_CONST = 40  # field 1: inner value of sub-message {field1: 40} (constant)

# Field 7 mirrors path_mm (NOT edge_mm as previously assumed).
# Confirmed by live data: field7=90 when path_mm=90, edge_mm=70.

# Field 4 contains pad direction in sub-field 2 ({f1: angle_degrees}).
# Sub-fields 3 and 5 within field 4 are device-fixed constants; we preserve
# them verbatim.  Observed bytes (little-endian protobuf):
#   f3 = 1a 04 0a 02 5a 5a   (field3, len=4, inner=[f1, len=2, 0x5a, 0x5a])
#   f5 = 28 5a               (field5, varint=90)
_FIELD4_SUFFIX = bytes.fromhex("1a040a025a5a285a")  # f3 + f5 (fixed)


# ══════════════════════════════════════════════════════════════════════════════
# Minimal protobuf encoder / decoder  (no external dependency)
# ══════════════════════════════════════════════════════════════════════════════


def _varint_encode(value: int) -> bytes:
    """Encode an integer as a protobuf varint.

    Supports negative values via protobuf int32 semantics: a negative value is
    treated as a 64-bit two's-complement unsigned integer (10-byte varint).
    If the edge distance field turns out to use sint32 (zigzag) encoding instead,
    replace with: value = (value << 1) ^ (value >> 31) before encoding.
    """
    if value < 0:
        value = value & 0xFFFFFFFFFFFFFFFF  # two's complement → 64-bit unsigned
    out: list[int] = []
    while True:
        bits = value & 0x7F
        value >>= 7
        if value:
            out.append(bits | 0x80)
        else:
            out.append(bits)
            break
    return bytes(out)


def _varint_decode(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a varint starting at *pos*. Returns (value, new_pos)."""
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _encode_field(field_num: int, wire_type: int, value: bytes | int) -> bytes:
    """Encode a single protobuf field (wire types 0=varint, 2=length-delimited)."""
    tag = _varint_encode((field_num << 3) | wire_type)
    if wire_type == 0:  # varint
        assert isinstance(value, int)
        return tag + _varint_encode(value)
    if wire_type == 2:  # length-delimited
        assert isinstance(value, (bytes, bytearray))
        return tag + _varint_encode(len(value)) + value
    raise ValueError(f"Unsupported wire type {wire_type}")


def _encode_speed_submsg(speed: str) -> bytes:
    """Encode a speed value as the body of a speed sub-message."""
    int_val = _SPEED_TO_INT.get(speed, 0)
    if int_val == 0:
        return b""  # slow → empty sub-message
    return _encode_field(1, 0, int_val)  # {field 1: 1 or 2}


def _encode_field4(pad_direction: int) -> bytes:
    """Encode DP155 field 4 body with the given pad direction angle.

    Structure: {f2: {f1: angle}, f3: fixed, f5: fixed}
    The f3 and f5 sub-fields are device constants preserved verbatim.
    """
    f2_inner = _encode_field(1, 0, pad_direction)   # {f1: angle}
    f2 = _encode_field(2, 2, f2_inner)              # field 2 = {f1: angle}
    return f2 + _FIELD4_SUFFIX


def _encode_dp155(
    edge_mm: int,
    path_mm: int,
    travel_speed: str,
    blade_speed: str,
    pad_direction: int = 90,
) -> str:
    """Encode the five cloud settings into a DP155 base64 blob."""
    payload = (
        _encode_field(
            1, 2, _encode_field(1, 0, _FIELD1_CONST)
        )  # field 1: sub-msg {f1:40}
        + _encode_field(
            2, 2, _encode_speed_submsg(travel_speed)
        )  # field 2: travel speed
        + _encode_field(3, 2, _encode_field(1, 0, edge_mm))  # field 3: edge dist (mm)
        + _encode_field(4, 2, _encode_field4(pad_direction))  # field 4: pad direction
        + _encode_field(5, 2, _encode_field(1, 0, path_mm))  # field 5: path dist (mm)
        + _encode_field(6, 2, _encode_speed_submsg(blade_speed))  # field 6: blade speed
        + _encode_field(7, 0, path_mm)  # field 7: mirrors path_mm
    )
    return base64.b64encode(payload).decode("ascii")


def _decode_dp155(blob: str) -> dict[str, Any]:
    """Decode a DP155 base64 blob and return the four cloud settings."""
    data = base64.b64decode(blob)
    pos = 0
    settings: dict[str, Any] = {}

    while pos < len(data):
        tag_val, pos = _varint_decode(data, pos)
        field_num = tag_val >> 3
        wire_type = tag_val & 0x07

        if wire_type == 0:  # varint — field 7 (= path_mm mirror); ignored on decode
            _val, pos = _varint_decode(data, pos)

        elif wire_type == 2:  # length-delimited
            length, pos = _varint_decode(data, pos)
            inner = data[pos : pos + length]
            pos += length

            if field_num in (2, 6):
                # Speed sub-message: inner field 1 = int (absent → 0 = slow)
                speed_val = 0
                if inner:
                    sub_tag, sub_pos = _varint_decode(inner, 0)
                    if (sub_tag >> 3) == 1 and (sub_tag & 7) == 0:
                        speed_val, _ = _varint_decode(inner, sub_pos)
                speed_str = _INT_TO_SPEED.get(speed_val, SPEED_SLOW)
                if field_num == 2:
                    settings["travel_speed"] = speed_str
                else:
                    settings["blade_speed"] = speed_str

            elif field_num in (3, 5):
                # Distance sub-message: inner field 1 = mm value (may be signed)
                mm_val = 0
                if inner:
                    sub_tag, sub_pos = _varint_decode(inner, 0)
                    if (sub_tag >> 3) == 1 and (sub_tag & 7) == 0:
                        mm_val, _ = _varint_decode(inner, sub_pos)
                # Convert uint64 varint back to signed int32 (two's complement)
                if mm_val >= (1 << 63):
                    mm_val -= 1 << 64
                if field_num == 3:
                    settings["edge_mm"] = mm_val
                else:
                    settings["path_mm"] = mm_val

            elif field_num == 4:
                # Pad direction sub-message: {f2: {f1: angle_degrees}, f3: fixed, f5: fixed}
                pad_val = 0
                f4_pos = 0
                while f4_pos < len(inner):
                    f4_tag, f4_pos = _varint_decode(inner, f4_pos)
                    f4_fn = f4_tag >> 3
                    f4_wt = f4_tag & 0x07
                    if f4_wt == 0:
                        _, f4_pos = _varint_decode(inner, f4_pos)  # skip f5
                    elif f4_wt == 2:
                        f4_ln, f4_pos = _varint_decode(inner, f4_pos)
                        f4_inner = inner[f4_pos : f4_pos + f4_ln]
                        f4_pos += f4_ln
                        if f4_fn == 2 and f4_inner:
                            # f4.f2 contains {f1: angle}
                            sub_tag, sub_pos = _varint_decode(f4_inner, 0)
                            if (sub_tag >> 3) == 1 and (sub_tag & 7) == 0:
                                pad_val, _ = _varint_decode(f4_inner, sub_pos)
                    else:
                        break
                settings["pad_direction"] = pad_val
                _LOGGER.debug("DP155 field4 pad_direction decoded: %d", pad_val)

        else:
            _LOGGER.warning(
                "DP155 unexpected wire type %d at field %d", wire_type, field_num
            )
            break

    return settings


# ══════════════════════════════════════════════════════════════════════════════
# DP154 — purpose unknown (NOT pad direction)
# ══════════════════════════════════════════════════════════════════════════════
#
# Live testing showed DP154 does NOT change when the app's pad direction dial
# is moved.  The pad direction is stored in DP155 field 4 (see above).
# DP154 is kept here for future investigation; the functions below are no
# longer called by the integration.
#
# Observed values:  0 → b'\x00' ("AA==");  1 → b'\x18\x01' ("GAE=")


def _encode_dp154(direction: int) -> str:
    """Encode a pad direction integer (0–359°) as a DP154 base64 blob.

    Device-native format:
        degrees 0 → b'\x00'  (single null byte, device quirk)
        degrees N → 0x18 + varint(N)  (field 3, wire type 0)
    """
    if direction == 0:
        return "AA=="

    # Manual encoding to match device format: field 3, wire type 0 (varint)
    # tag = ((field_num << 3) | wire_type) = (3 << 3) | 0 = 0x18 = 24
    tag = 0x18
    if direction < 128:
        value_byte = bytes([direction])
    else:
        value_byte = _varint_encode(direction)
    payload = bytes([tag]) + value_byte

    return base64.b64encode(payload).decode("ascii")


def _decode_dp154(blob: str) -> int:
    """Decode a DP154 base64 blob → pad direction integer."""
    data = base64.b64decode(blob)
    if not data or data == b"\x00":
        _LOGGER.debug("DP154 decode: null → 0")
        return 0
    try:
        pos = 0
        tag_val, pos = _varint_decode(data, pos)
        field_num = tag_val >> 3
        wire_type = tag_val & 0x07
        if field_num == 3 and wire_type == 0:
            val, _ = _varint_decode(data, pos)
            _LOGGER.debug("DP154 decode: field3=%d", val)
            return val
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning("DP154 decode failed for blob %r: %s", blob, e)
    _LOGGER.debug("DP154 decode: fallback → 0 for blob %r (raw: %s)", blob, data.hex())
    return 0


# ══════════════════════════════════════════════════════════════════════════════
# Eufy / Tuya mobile API helpers
# ══════════════════════════════════════════════════════════════════════════════


def _generate_device_id() -> str:
    """Generate a pseudo-random 44-char Tuya device ID (mimics Android SDK)."""
    prefix = "8534c8ec0ed0"  # MD5-based prefix from a Google Pixel AVD
    chars = string.ascii_letters + string.digits
    return prefix + "".join(random.choice(chars) for _ in range(44 - len(prefix)))


def _shuffled_md5(value: str) -> str:
    """MD5 hash with byte-order shuffle used in Tuya request signing."""
    h = hashlib.md5(value.encode("utf-8")).hexdigest()
    return h[8:16] + h[0:8] + h[24:32] + h[16:24]


def _get_signature(query_params: dict[str, str], encoded_post_data: str) -> str:
    """Compute the HMAC-SHA256 request signature for the Tuya mobile API."""
    params = dict(query_params)
    if encoded_post_data:
        params["postData"] = encoded_post_data

    pairs = sorted(
        (k, _shuffled_md5(v) if k == "postData" else v)
        for k, v in params.items()
        if k in _SIGNATURE_PARAMS
    )
    message = "||".join(f"{k}={v}" for k, v in pairs)
    return _hmac_module.new(
        _TUYA_HMAC_KEY, message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _determine_password(username: str) -> str:
    """Derive the Tuya login password from the Eufy UID via AES-128-CBC + MD5."""
    from cryptography.hazmat.backends.openssl import backend as openssl_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    padded_size = 16 * math.ceil(len(username) / 16)
    password_uid = username.zfill(padded_size)

    cipher = Cipher(
        algorithms.AES(_TUYA_PASSWORD_KEY),
        modes.CBC(_TUYA_PASSWORD_IV),
        backend=openssl_backend,
    )
    enc = cipher.encryptor()
    encrypted = enc.update(password_uid.encode("utf-8")) + enc.finalize()
    return hashlib.md5(encrypted.hex().upper().encode("utf-8")).hexdigest()


def _unpadded_rsa(exponent: int, n: int, plaintext: bytes) -> bytes:
    """Raw (no-padding) RSA encryption used for Tuya password handshake."""
    key_length = math.ceil(n.bit_length() / 8)
    m = int.from_bytes(plaintext, "big")
    c = pow(m, exponent, n)
    return c.to_bytes(key_length, "big")


# ══════════════════════════════════════════════════════════════════════════════
# EufyCloudClient
# ══════════════════════════════════════════════════════════════════════════════


class EufyCloudClient:
    """Synchronous client for reading/writing Eufy cloud settings via Tuya mobile API.

    All public methods are blocking and must be called from an executor thread.
    """

    def __init__(self, email: str, password: str, device_id: str) -> None:
        self._email = email
        self._password = password
        self._device_id = device_id

        # Eufy REST session state
        self._eufy_token: str | None = None
        self._eufy_uid: str | None = None
        self._eufy_base_url = _EUFY_BASE_URL

        # Tuya mobile API session state
        self._tuya_session_id: str | None = None
        self._tuya_base_url = _TUYA_INITIAL_URL
        self._tuya_username: str | None = None
        self._tuya_country: str | None = None
        self._tuya_device_id = _generate_device_id()

        # Separate HTTP sessions for Eufy REST and Tuya API
        self._eufy_session = requests.Session()
        self._eufy_session.headers.update(
            {
                "User-Agent": "EufyHome-Android-2.4.0",
                "timezone": _EUFY_TIMEZONE,
                "category": "Home",
                "token": "",
                "uid": "",
                "openudid": _EUFY_PLATFORM,
                "clientType": "2",
                "language": _EUFY_LANGUAGE,
                "country": "US",
                "Accept-Encoding": "gzip",
            }
        )
        self._tuya_session = requests.Session()
        self._tuya_session.headers.update(
            {"User-Agent": "TY-UA=APP/Android/2.4.0/SDK/null"}
        )

    # ── Eufy login ────────────────────────────────────────────────────────────

    def _eufy_login(self) -> None:
        """Log in to the Eufy REST API and populate session credentials."""
        resp = self._eufy_session.post(
            self._eufy_base_url + "user/email/login",
            json={
                "client_Secret": _EUFY_CLIENT_SECRET,
                "client_id": _EUFY_CLIENT_ID,
                "email": self._email,
                "password": self._password,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        self._eufy_token = data["access_token"]
        self._eufy_uid = data["user_info"]["id"]
        # The response contains the correct regional base URL for subsequent calls
        self._eufy_base_url = data["user_info"]["request_host"]

        self._eufy_session.headers.update(
            {"uid": self._eufy_uid, "token": self._eufy_token}
        )

        self._tuya_username = f"eh-{self._eufy_uid}"
        self._tuya_country = data["user_info"].get("phone_code", "31")

    def _ensure_eufy_session(self) -> None:
        if not self._eufy_token:
            self._eufy_login()

    # ── Tuya mobile API ───────────────────────────────────────────────────────

    def _tuya_request(
        self,
        action: str,
        version: str = "1.0",
        data: dict | None = None,
        extra_query: dict | None = None,
        requires_session: bool = True,
    ) -> Any:
        """Send a signed request to the Tuya mobile API and return result.

        *extra_query* adds additional URL query parameters (e.g. ``{"gid": "123"}``
        for the device-list endpoint).
        """
        if requires_session and not self._tuya_session_id:
            self._ensure_eufy_session()
            self._tuya_acquire_session()

        query: dict[str, str] = {
            **_DEFAULT_TUYA_PARAMS,
            "deviceId": self._tuya_device_id,
            "time": str(int(time.time())),
            "requestId": str(uuid.uuid4()),
            "a": action,
            "v": version,
            **(extra_query or {}),
        }
        if self._tuya_session_id:
            query["sid"] = self._tuya_session_id

        post_data = json.dumps(data, separators=(",", ":")) if data else ""
        sign = _get_signature(query, post_data)

        resp = self._tuya_session.post(
            self._tuya_base_url + "/api.json",
            params={**query, "sign": sign},
            data={"postData": post_data} if post_data else None,
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()

        if "result" not in payload:
            raise RuntimeError(f"Tuya API error response: {payload}")

        return payload["result"]

    def _tuya_acquire_session(self) -> None:
        """Authenticate with Tuya and store the session ID + regional base URL."""
        password = _determine_password(self._tuya_username)

        # Step 1: get an RSA public key + challenge token
        token_resp = self._tuya_request(
            "tuya.m.user.uid.token.create",
            data={"uid": self._tuya_username, "countryCode": self._tuya_country},
            requires_session=False,
        )

        # Step 2: encrypt password with the server's RSA public key
        encrypted_pw = _unpadded_rsa(
            exponent=int(token_resp["exponent"]),
            n=int(token_resp["publicKey"]),
            plaintext=password.encode("utf-8"),
        )

        # Step 3: log in and obtain a session ID
        session_resp = self._tuya_request(
            "tuya.m.user.uid.password.login.reg",
            data={
                "uid": self._tuya_username,
                "createGroup": True,
                "ifencrypt": 1,
                "passwd": encrypted_pw.hex(),
                "countryCode": self._tuya_country,
                "options": '{"group": 1}',
                "token": token_resp["token"],
            },
            requires_session=False,
        )

        self._tuya_session_id = session_resp["sid"]
        # Switch to the correct regional API endpoint
        self._tuya_base_url = session_resp["domain"]["mobileApiUrl"]

    def _invalidate_sessions(self) -> None:
        """Clear cached session tokens so the next call triggers re-authentication."""
        self._tuya_session_id = None
        self._eufy_token = None

    def _tuya_request_with_retry(self, *args, **kwargs) -> Any:
        """Call _tuya_request; on failure invalidate sessions and retry once."""
        try:
            return self._tuya_request(*args, **kwargs)
        except Exception:
            _LOGGER.warning(
                "Tuya API call failed, invalidating sessions and retrying once"
            )
            self._invalidate_sessions()
            return self._tuya_request(*args, **kwargs)

    # ── Public API ────────────────────────────────────────────────────────────

    def list_all_devices(self) -> list[dict]:
        """Return all Tuya-connected devices across all homes (+ shared).

        Each dict has at minimum ``devId``, ``localKey``, ``name``.
        Only devices that expose a ``localKey`` are included.
        """
        # ── 1. Own devices (listed per home) ──────────────────────────────────
        homes: list[dict] = (
            self._tuya_request_with_retry("tuya.m.location.list", version="2.1") or []
        )

        seen_ids: set[str] = set()
        devices: list[dict] = []

        for home in homes:
            home_id = str(home.get("groupId") or home.get("locationId") or "")
            if not home_id:
                continue
            try:
                home_devices = (
                    self._tuya_request_with_retry(
                        "tuya.m.my.group.device.list",
                        version="1.0",
                        extra_query={"gid": home_id},
                    )
                    or []
                )
                for d in home_devices:
                    dev_id = d.get("devId")
                    if dev_id and dev_id not in seen_ids and d.get("localKey"):
                        seen_ids.add(dev_id)
                        devices.append(d)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Failed to list devices for home %s: %s", home_id, exc)

        # ── 2. Shared devices ─────────────────────────────────────────────────
        try:
            shared: list[dict] = (
                self._tuya_request_with_retry(
                    "tuya.m.my.shared.device.list", version="1.0"
                )
                or []
            )
            for d in shared:
                dev_id = d.get("devId")
                if dev_id and dev_id not in seen_ids and d.get("localKey"):
                    seen_ids.add(dev_id)
                    devices.append(d)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to list shared devices: %s", exc)

        _LOGGER.debug("Discovered %d devices with local keys", len(devices))
        return devices

    def get_settings(self) -> dict[str, Any]:
        """Fetch DP155 from the cloud and return all decoded settings.

        Returns a dict with keys:
            edge_mm, path_mm, travel_speed, blade_speed, pad_direction  (all from DP155)

        pad_direction is an integer angle in degrees (0–359).
        Reference: 0 = west (9 o'clock), 90 = north (12 o'clock),
                   180 = east (3 o'clock), 270 = south (6 o'clock).
        """
        dps = self._tuya_request_with_retry(
            "tuya.m.device.dp.get",
            data={"devId": self._device_id},
        )
        blob155 = dps.get("155")
        if not blob155:
            raise RuntimeError(
                "DP155 not present in cloud response — "
                f"available DPS: {list(dps.keys())}"
            )
        settings = _decode_dp155(blob155)

        # Ensure pad_direction always present (default 90 = north if field 4 absent)
        settings.setdefault("pad_direction", 90)

        _LOGGER.debug("Cloud settings decoded: %s", settings)
        return settings

    def set_settings(
        self,
        edge_mm: int | None = None,
        path_mm: int | None = None,
        travel_speed: str | None = None,
        blade_speed: str | None = None,
        pad_direction: int | None = None,
    ) -> None:
        """Write updated cloud settings to DP155.

        All five settings are always written together.  Unchanged fields are
        read from the device first to preserve them.
        """
        # Read current values so we can fill in any unchanged fields.
        current = self.get_settings()

        new_edge_mm = edge_mm if edge_mm is not None else current["edge_mm"]
        new_path_mm = path_mm if path_mm is not None else current["path_mm"]
        new_travel_speed = (
            travel_speed if travel_speed is not None else current["travel_speed"]
        )
        new_blade_speed = (
            blade_speed if blade_speed is not None else current["blade_speed"]
        )
        new_pad_direction = (
            pad_direction if pad_direction is not None else current["pad_direction"]
        )

        blob155 = _encode_dp155(
            new_edge_mm, new_path_mm, new_travel_speed, new_blade_speed,
            new_pad_direction,
        )
        _LOGGER.debug(
            "Publishing DP155: edge=%dmm path=%dmm travel=%s blade=%s pad=%d°",
            new_edge_mm,
            new_path_mm,
            new_travel_speed,
            new_blade_speed,
            new_pad_direction,
        )
        self._tuya_request_with_retry(
            "tuya.m.device.dp.publish",
            data={
                "devId": self._device_id,
                "gwId": self._device_id,
                "uid": self._tuya_username,
                "dps": {"155": blob155},
            },
        )
