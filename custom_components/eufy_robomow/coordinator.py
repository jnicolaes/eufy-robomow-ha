"""DataUpdateCoordinator for Eufy Robomow."""

from __future__ import annotations

import asyncio
import logging
import time
from threading import Lock
from homeassistant.util import dt as dt_util

from .commands import MowerCommand
from .sessions import SessionStore
from .const import CMD_START, CMD_RESUME, CMD_PAUSE, CMD_DOCK
from datetime import datetime, timedelta

import tinytuya

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    TUYA_VERSION,
    POLL_INTERVAL,
    CLOUD_POLL_INTERVAL,
    _CLOUD_MAX_BACKOFF,
    CLOUD_EDGE_MM,
    CLOUD_PATH_MM,
    CLOUD_TRAVEL_SPEED,
    CLOUD_BLADE_SPEED,
    CLOUD_PAD_DIRECTION,
    OPERATING_MODE_CONTROL,
)

_LOGGER = logging.getLogger(__name__)

# After this many consecutive local-poll errors we recreate the tinytuya
# Device object to flush any stale socket / connection state.
_MAX_CONSECUTIVE_ERRORS = 5


class EufyMowerCoordinator(DataUpdateCoordinator[dict]):
    """Polls the Eufy E15 via Tuya local protocol every POLL_INTERVAL seconds.

    If an EufyCloudClient is provided, cloud DPS are also fetched every
    CLOUD_POLL_INTERVAL seconds (default 5 min).  ALL raw cloud DPS are merged
    into coordinator.data so every DP is visible as a HA sensor — even cloud-only
    ones like DP3, DP4, DP36, DP102-DP185 that the local Tuya protocol never
    returns.  For any DP present in both transports the local (real-time) value
    takes precedence.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        device_id: str,
        local_key: str,
        cloud_client=None,  # EufyCloudClient | None  (avoid circular import)
        operating_mode: str = "observe_only",
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self.host = host
        self.device_id = device_id
        self.local_key = local_key
        self.cloud_client = cloud_client
        self.operating_mode = operating_mode

        self._device_lock = Lock()
        self.local_dps: dict = {}
        self.local_generation = 0
        self.last_local_update: datetime | None = None
        self.command: MowerCommand | None = None
        self.session_store: SessionStore | None = None
        self._device = self._make_device()
        # Use float('-inf') so the first poll always fetches cloud DPS
        self._cloud_last_fetch: float = float("-inf")
        # Consecutive cloud failures — used for exponential backoff
        self._cloud_consecutive_failures: int = 0
        # Track consecutive local-poll failures to know when to recreate the device
        self._consecutive_errors: int = 0
        # Set of all DP keys seen so far (local + cloud), used to detect new DPs
        self._known_dps: set[str] = set()
        # Callbacks invoked when previously-unseen DPS keys appear in a poll.
        # Registered by sensor.py so it can add generic sensors on-the-fly.
        # Callbacks receive the current dps dict as their sole argument.
        self._new_dp_callbacks: list = []

    @property
    def control_enabled(self) -> bool:
        """Return whether physical and settings writes are explicitly enabled."""
        return self.operating_mode == OPERATING_MODE_CONTROL

    def _require_control_enabled(self) -> None:
        """Reject writes while the integration is in its safe default mode."""
        if not self.control_enabled:
            raise HomeAssistantError(
                "Eufy Robomow is in observe-only mode. Enable control in the "
                "integration options before sending commands."
            )

    def async_add_new_dp_listener(self, callback) -> None:
        """Register *callback(dps)* to be called whenever new DPS keys are discovered.

        The callback receives the current complete DPS dict as its argument so it
        can inspect the latest values without waiting for coordinator.data to update.
        """
        self._new_dp_callbacks.append(callback)

    def _make_device(self) -> tinytuya.Device:
        d = tinytuya.Device(
            self.device_id,
            self.host,
            self.local_key,
            version=TUYA_VERSION,
        )
        d.set_socketTimeout(5)
        # Non-persistent: close the TCP socket after every request.
        # Persistent mode (the default in some tinytuya versions) keeps the
        # socket open between polls.  When that socket silently dies it is never
        # freed, causing file-descriptor leaks and eventually OOM / CPU spikes.
        d.set_socketPersistent(False)
        return d

    def _device_call(self, method: str, *args):
        """Serialize worker calls even if an awaiting task is cancelled."""
        with self._device_lock:
            return getattr(self._device, method)(*args)

    # ── polling ───────────────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict:
        """Fetch DPS from device (local) and optionally from cloud.

        Strategy:
          1. Local poll  — always, every POLL_INTERVAL seconds (~10 s).
          2. Cloud poll  — every CLOUD_POLL_INTERVAL seconds (~5 min).
             • Fetches ALL raw cloud DPS via tuya.m.device.dp.get.
             • Merges them into the dict: local values win for any DP in both.
             • Decoded DP155 settings are stored under cloud_* keys.
          3. Carry-forward — when cloud poll is not due (or fails), all keys from
             the previous coordinator.data that are not in the fresh local dps are
             carried forward so cloud-only entities never go unavailable.
          4. New-DP detection — after the full merge, new DPS keys fire the
             _new_dp_callbacks with the complete dps dict so sensor.py can
             register generic sensors immediately.
        """
        # ── 1. Local DPS (every POLL_INTERVAL seconds) ────────────────────────
        sample_started = time.monotonic()
        try:
            result = await self.hass.async_add_executor_job(self._device_call, "status")
        except Exception as exc:  # noqa: BLE001
            self._consecutive_errors += 1
            if self._consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                _LOGGER.debug(
                    "Recreating tinytuya device after %d consecutive errors",
                    self._consecutive_errors,
                )
                self._device = self._make_device()
                self._consecutive_errors = 0
            raise UpdateFailed(f"Tuya connection error: {exc}") from exc

        if not isinstance(result, dict) or not isinstance(result.get("dps"), dict):
            raise UpdateFailed("No valid local mower telemetry received")

        if "Error" in result:
            err = result["Error"]
            _LOGGER.debug("Tuya poll error: %s (%s)", err, result.get("Err"))
            self._consecutive_errors += 1
            if self._consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                _LOGGER.debug(
                    "Recreating tinytuya device after %d consecutive errors",
                    self._consecutive_errors,
                )
                self._device = self._make_device()
                self._consecutive_errors = 0
            raise UpdateFailed(f"Tuya error: {err}")

        self._consecutive_errors = 0
        dps: dict = result.get("dps", {})
        local_dp_keys: set[str] = set(dps.keys())
        command_pending = self.command and self.command.state in ("sending", "pending")
        self.local_dps = dict(dps)
        self.local_generation += 1
        self.last_local_update = dt_util.utcnow()
        if self.command and sample_started > self.command.sent_monotonic:
            self.command.observe(self.local_generation, self.local_dps)
        if self.session_store:
            self.session_store.observe(self.local_dps, self.last_local_update)

        _LOGGER.debug("Local DPS update received (%d keys)", len(dps))

        # ── 2. Cloud DPS + settings (every CLOUD_POLL_INTERVAL seconds) ───────
        # Two guards before attempting a cloud poll:
        #   a) Sun above horizon — mower cannot operate at night, no point refreshing.
        #      Uses HA's sun.sun entity; if unavailable defaults to allowing the poll.
        #   b) Exponential backoff on consecutive failures (5 min → 10 → 20 → 40 → 60).
        #      _cloud_last_fetch is updated on BOTH success AND failure so a failed
        #      login is not retried every 10 s (which would hammer the Eufy API and
        #      interfere with other integrations sharing the same account).
        now = time.monotonic()
        if command_pending:
            self._carry_forward_cloud_data(dps, local_dp_keys)
        elif self.cloud_client is not None:
            _sun = self.hass.states.get("sun.sun")
            _sun_below_horizon = _sun is not None and _sun.state == "below_horizon"

            if _sun_below_horizon:
                _LOGGER.debug("Sun below horizon — skipping cloud poll")
                self._carry_forward_cloud_data(dps, local_dp_keys)
            else:
                backoff = min(
                    CLOUD_POLL_INTERVAL * (2**self._cloud_consecutive_failures),
                    _CLOUD_MAX_BACKOFF,
                )
                if now - self._cloud_last_fetch >= backoff:
                    try:
                        raw_cloud_dps, cloud_settings = await self.hass.async_add_executor_job(
                            self.cloud_client.get_all_dps
                        )
                        # Merge: add every cloud DP that is NOT already in local dps.
                        # Local values take precedence (more real-time).
                        for dp_key, dp_val in raw_cloud_dps.items():
                            if dp_key not in local_dp_keys:
                                dps[dp_key] = dp_val

                        # Decoded DP155 settings under cloud_* keys
                        dps[CLOUD_EDGE_MM] = cloud_settings["edge_mm"]
                        dps[CLOUD_PATH_MM] = cloud_settings["path_mm"]
                        dps[CLOUD_TRAVEL_SPEED] = cloud_settings["travel_speed"]
                        dps[CLOUD_BLADE_SPEED] = cloud_settings["blade_speed"]
                        dps[CLOUD_PAD_DIRECTION] = cloud_settings["pad_direction"]

                        self._cloud_last_fetch = now
                        self._cloud_consecutive_failures = 0
                        _LOGGER.debug(
                            "Cloud poll: %d raw DPS merged, settings=%s",
                            len(raw_cloud_dps),
                            cloud_settings,
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._cloud_consecutive_failures += 1
                        next_retry = min(
                            CLOUD_POLL_INTERVAL * (2**self._cloud_consecutive_failures),
                            _CLOUD_MAX_BACKOFF,
                        )
                        _LOGGER.warning(
                            "Cloud DPS fetch failed (attempt %d, next retry in %ds): %s",
                            self._cloud_consecutive_failures,
                            next_retry,
                            exc,
                        )
                        # Update timestamp so the failed attempt is not retried on the
                        # next local poll (10 s); retry honours the backoff window.
                        self._cloud_last_fetch = now
                        self._carry_forward_cloud_data(dps, local_dp_keys)
                else:
                    # Not yet due — carry forward previous cloud data
                    self._carry_forward_cloud_data(dps, local_dp_keys)

        # ── 3. New-DP detection (after full merge) ────────────────────────────
        current_all_keys = set(dps.keys())
        new_keys = current_all_keys - self._known_dps

        def _dp_sort_key(k: str):
            # Numeric DP keys sort before string keys (e.g. "cloud_edge_mm").
            # Returns a (int, str) tuple so comparisons are always type-compatible.
            return (0, int(k)) if k.isdigit() else (1, k)

        if new_keys:
            _LOGGER.info(
                "New DPS discovered: %s",
                sorted(new_keys, key=_dp_sort_key),
            )
            # Pass the current full dps dict so callbacks don't read stale data
            for cb in list(self._new_dp_callbacks):
                cb(dps)
        self._known_dps = current_all_keys

        return dps

    def _carry_forward_cloud_data(self, dps: dict, local_dp_keys: set[str]) -> None:
        """Copy all non-local keys from the previous coordinator.data into dps.

        This keeps cloud-only DP sensors from going unavailable between cloud polls.
        Local DP values already in dps are never overwritten.
        """
        if not self.data:
            return
        for key, val in self.data.items():
            if key not in local_dp_keys:
                dps[key] = val

    # ── commands ──────────────────────────────────────────────────────────────

    async def async_send_mower_command(self, action: str, timeout: float = 35) -> None:
        """Send once, then await fresh device telemetry with a bounded timeout."""
        self._require_control_enabled()
        if action not in ("start", "resume", "pause", "dock"):
            raise HomeAssistantError("Unsupported mower command")
        if self.command and self.command.state in ("sending", "pending"):
            if action in ("start", "resume"):
                raise HomeAssistantError("A mower command is already pending")
            self.command.finish("superseded")
        operation = MowerCommand(action, self.local_generation)
        self.command = operation
        self.async_update_listeners()
        dp, value = {
            "start": CMD_START,
            "resume": CMD_RESUME,
            "pause": CMD_PAUSE,
            "dock": CMD_DOCK,
        }[action]
        try:
            result = await self.hass.async_add_executor_job(
                self._device_call, "set_value", int(dp), value
            )
            if operation.state == "superseded":
                raise HomeAssistantError("Command superseded by a later safety command")
            if isinstance(result, dict) and "Error" in result:
                operation.finish("rejected")
                raise HomeAssistantError("The mower rejected the command")
            # Ignore every poll that began before the write finished.
            operation.after_generation = self.local_generation
            operation.sent_monotonic = time.monotonic()
            operation.state = "pending"
            self.async_update_listeners()
            async with asyncio.timeout(timeout):
                await self.async_request_refresh()
                await operation.event.wait()
            if operation.state == "superseded":
                raise HomeAssistantError("Command superseded by a later safety command")
        except TimeoutError as exc:
            operation.finish("timeout")
            raise HomeAssistantError(
                "Command sent, but the mower did not confirm its state within 35 seconds. "
                "It may still execute; check the mower before retrying."
            ) from exc
        except asyncio.CancelledError:
            operation.finish("interrupted")
            raise
        except HomeAssistantError:
            raise
        except Exception as exc:
            operation.finish("failed")
            raise HomeAssistantError("Could not send the mower command") from exc
        finally:
            self.async_update_listeners()

    async def async_send_command(self, dp: str, value) -> None:
        """Write a single DPS value or raise a visible Home Assistant error."""
        self._require_control_enabled()
        _LOGGER.debug("Sending command DP %s = %s", dp, value)
        try:
            result = await self.hass.async_add_executor_job(
                self._device_call, "set_value", int(dp), value
            )
            if isinstance(result, dict) and "Error" in result:
                raise HomeAssistantError(f"Mower rejected command DP {dp}: {result['Error']}")
            _LOGGER.debug("Command result: %s", result)
            # Immediately refresh state
            await self.async_request_refresh()
        except HomeAssistantError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"Mower command DP {dp} failed: {exc}") from exc

    async def async_set_cloud_setting(self, **kwargs) -> None:
        """Write one or more cloud settings via the Tuya mobile API.

        Keyword arguments: edge_mm, path_mm, travel_speed, blade_speed, pad_direction.
        Raises a visible Home Assistant error when the write is not confirmed.
        """
        self._require_control_enabled()
        if not self.cloud_client:
            raise HomeAssistantError(
                "Cloud settings are unavailable because no cloud client is configured."
            )

        def _do_set() -> None:
            self.cloud_client.set_settings(**kwargs)

        try:
            await self.hass.async_add_executor_job(_do_set)
            # Small delay to let cloud process the write before re-fetching
            await asyncio.sleep(1)
            # Force a cloud re-fetch on the next poll cycle
            self._cloud_last_fetch = float("-inf")
            await self.async_request_refresh()
        except Exception as exc:  # noqa: BLE001
            exc_str = str(exc)
            if "DEVICE_OFFLINE" in exc_str or "offline" in exc_str.lower():
                _LOGGER.warning(
                    "Cannot update cloud setting: device is offline "
                    "(command will not be queued; retry when mower is running)"
                )
            else:
                _LOGGER.error("Cloud setting update failed: %s", exc)
            raise HomeAssistantError(f"Cloud setting update failed: {exc}") from exc
