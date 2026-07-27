"""Config flow for Eufy Robomow.

Two-step setup:
  Step 1 (user)   — Eufy email + password → authenticates and discovers devices.
  Step 2 (device) — Pick a device from the list + enter its local IP address.

The device_id and local_key are retrieved automatically from the cloud;
the user never has to run external tools.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    CONF_LOCAL_KEY,
    CONF_EUFY_EMAIL,
    CONF_EUFY_PASSWORD,
    CONF_OPERATING_MODE,
    DEFAULT_OPERATING_MODE,
    OPERATING_MODES,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EUFY_EMAIL): str,
        vol.Required(CONF_EUFY_PASSWORD): str,
    }
)


def _discover_devices(email: str, password: str) -> list[dict]:
    """Log in and return all discoverable devices. Raises on failure."""
    from .cloud import EufyCloudClient
    client = EufyCloudClient(email=email, password=password, device_id="")
    devices = client.list_all_devices()
    if not devices:
        raise NoDevicesFound("No devices with a local key found in this account")
    return devices


def _test_local_connection(host: str, device_id: str, local_key: str) -> None:
    """Try to connect to the device via Tuya local protocol. Raises CannotConnect."""
    import tinytuya
    from .const import TUYA_VERSION
    d = tinytuya.Device(device_id, host, local_key, version=TUYA_VERSION)
    d.set_socketTimeout(5)
    result = d.status()
    if "Error" in result:
        raise CannotConnect(result["Error"])
    if "dps" not in result:
        raise CannotConnect("No DPS in response")


def _device_label(device: dict) -> str:
    """Human-readable label for a device entry in the picker dropdown."""
    name   = device.get("name") or device.get("productName") or "Unknown device"
    dev_id = device["devId"]
    return f"{name}  [{dev_id[:8]}…]"


class EufyRobomowConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the two-step setup flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._email:      str        = ""
        self._password:   str        = ""
        self._discovered: list[dict] = []  # Tuya device dicts

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> EufyRobomowOptionsFlow:
        """Create the options flow."""
        return EufyRobomowOptionsFlow()

    # ── Step 1: credentials ───────────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect Eufy credentials and discover devices."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email    = user_input[CONF_EUFY_EMAIL].strip()
            password = user_input[CONF_EUFY_PASSWORD].strip()

            try:
                devices = await self.hass.async_add_executor_job(
                    _discover_devices, email, password
                )
            except NoDevicesFound:
                errors["base"] = "no_devices"
            except ValueError:
                # Explicit login failure (wrong credentials or server rejection)
                _LOGGER.exception("Eufy login rejected")
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                # Transient errors: network issues, timeouts, 5xx responses
                _LOGGER.exception("Unexpected error during device discovery")
                errors["base"] = "unknown"

            if not errors:
                self._email      = email
                self._password   = password
                self._discovered = devices
                return await self.async_step_device()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    # ── Step 2: device selection ──────────────────────────────────────────────

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick a device and enter its local IP."""
        errors: dict[str, str] = {}

        # Build the dropdown options {devId: "Name [id…]"}
        options = {d["devId"]: _device_label(d) for d in self._discovered}

        # Auto-select when there is only one device.
        # Note: the cloud API's "ip" field reports the IP the device last connected
        # FROM, which is often the router's public IP or a 4G address — not the
        # device's LAN IP.  We intentionally leave CONF_HOST blank so the user
        # enters the correct internal address.
        auto_id = self._discovered[0]["devId"] if len(self._discovered) == 1 else None

        step_schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ID): vol.In(options),
                vol.Required(CONF_HOST): str,
            }
        )

        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID]
            host      = user_input[CONF_HOST].strip()

            # Retrieve the local key for the selected device
            device    = next(d for d in self._discovered if d["devId"] == device_id)
            local_key = device["localKey"]

            # Verify local connectivity
            try:
                await self.hass.async_add_executor_job(
                    _test_local_connection, host, device_id, local_key
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during local connection test")
                errors["base"] = "unknown"

            if not errors:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()

                device_name = device.get("name") or device.get("productName") or "Eufy E15"
                return self.async_create_entry(
                    title=f"{device_name} ({host})",
                    data={
                        CONF_HOST:          host,
                        CONF_DEVICE_ID:     device_id,
                        CONF_LOCAL_KEY:     local_key,
                        CONF_EUFY_EMAIL:    self._email,
                        CONF_EUFY_PASSWORD: self._password,
                        CONF_OPERATING_MODE: DEFAULT_OPERATING_MODE,
                    },
                )

        # Pre-populate the form with smart defaults (device ID only; host is manual)
        suggested: dict[str, Any] = {}
        if auto_id:
            suggested[CONF_DEVICE_ID] = auto_id

        return self.async_show_form(
            step_id="device",
            data_schema=self.add_suggested_values_to_schema(step_schema, suggested),
            errors=errors,
            description_placeholders={"device_count": str(len(self._discovered))},
        )


class EufyRobomowOptionsFlow(OptionsFlowWithReload):
    """Manage safety-sensitive integration options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure the integration operating mode."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_mode = self.config_entry.options.get(
            CONF_OPERATING_MODE,
            self.config_entry.data.get(CONF_OPERATING_MODE, DEFAULT_OPERATING_MODE),
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_OPERATING_MODE): vol.In(OPERATING_MODES),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_OPERATING_MODE: current_mode}
            ),
        )


# ── Custom exceptions ──────────────────────────────────────────────────────────

class CannotConnect(HomeAssistantError):
    """Cannot connect to the device via local Tuya protocol."""


class NoDevicesFound(HomeAssistantError):
    """No Tuya-compatible devices with local keys found in this account."""
