"""Render the actual installed planning templates against HA state scenarios."""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

PACKAGE = yaml.safe_load(
    (Path(__file__).parents[1] / "examples/eufy_mower_planning.yaml").read_text()
)
CONDITIONS = PACKAGE["template"][0]["sensor"][0]["state"]
PLANNING = PACKAGE["template"][0]["sensor"][1]["state"]


async def render_scenario(tmp_path, overrides=None):
    hass = HomeAssistant(str(tmp_path))
    now = dt_util.utcnow()
    radar = {"success": True, "start": now.timestamp() - 60, "delta": 300, "precip": [0] * 25}
    states = {
        "input_boolean.eufy_planning_ingesteld": ("on", {}),
        "switch.example_irrigation_valve": ("off", {}),
        "input_boolean.example_irrigation_active": ("off", {}),
        "input_boolean.example_irrigation_planned": ("off", {}),
        "sensor.example_buienalarm_rain_data": ("0", {"data": radar}),
        "input_number.eufy_maximale_sessieduur": ("90", {}),
        "input_boolean.eufy_automatisch_maaien": ("on", {}),
        "input_select.eufy_maaidagen": ("Dagelijks", {}),
        "input_datetime.eufy_maaivenster_start": ("00:00:00", {}),
        "input_datetime.eufy_maaivenster_einde": ("23:59:59", {}),
        "input_datetime.eufy_laatste_natte_waarneming": (
            (now - timedelta(hours=2)).isoformat(),
            {},
        ),
        "input_datetime.eufy_laatste_startpoging": ((now - timedelta(days=1)).isoformat(), {}),
        "sun.sun": ("above_horizon", {"next_setting": (now + timedelta(hours=5)).isoformat()}),
        "lawn_mower.eufy_robomow_e15_mower": (
            "docked",
            {"telemetry_updated_at": now.isoformat(), "operating_mode": "control"},
        ),
        "sensor.eufy_robomow_e15_battery": ("100", {}),
        "input_number.eufy_minimale_startbatterij": ("80", {}),
        "input_number.eufy_droogtijd_minuten": ("60", {}),
        "switch.eufy_robomow_e15_stop_on_rain_detection": ("on", {}),
        "switch.eufy_robomow_e15_child_protection": ("on", {}),
    }
    if overrides:
        states.update(overrides(now, radar))
    for entity_id, (state, attrs) in states.items():
        hass.states.async_set(entity_id, state, attrs)
    conditions = Template(CONDITIONS, hass).async_render()
    hass.states.async_set("sensor.eufy_maaiomstandigheden", conditions)
    planning = Template(PLANNING, hass).async_render()
    await hass.async_stop()
    return conditions, planning


def test_dry_conditions_allow_one_start(tmp_path):
    assert asyncio.run(render_scenario(tmp_path)) == (
        "Droog volgens regenradar",
        "Klaar om te maaien",
    )


@pytest.mark.parametrize(
    "overrides,expected",
    [
        (
            lambda n, r: {"input_boolean.eufy_automatisch_maaien": ("off", {})},
            "Automatisch maaien staat uit",
        ),
        (
            lambda n, r: {"switch.example_irrigation_valve": ("unavailable", {})},
            "Beregening actief of klepstatus onbekend",
        ),
        (
            lambda n, r: {
                "sensor.example_buienalarm_rain_data": (
                    "0",
                    {"data": {**r, "start": n.timestamp() - 1800}},
                )
            },
            "Regenverwachting ontbreekt of is verouderd",
        ),
        (
            lambda n, r: {
                "sensor.example_buienalarm_rain_data": ("0", {"data": {**r, "precip": [0] * 5}})
            },
            "Regenverwachting dekt de hele maaisessie niet",
        ),
        (
            lambda n, r: {
                "sensor.example_buienalarm_rain_data": (
                    "0",
                    {"data": {**r, "precip": [0] * 5 + [1] + [0] * 19}},
                )
            },
            "Regen gemeld of verwacht",
        ),
        (
            lambda n, r: {"input_datetime.eufy_laatste_startpoging": (n.isoformat(), {})},
            "Vandaag al een startpoging gedaan",
        ),
        (
            lambda n, r: {"input_datetime.eufy_laatste_natte_waarneming": (n.isoformat(), {})},
            "Wacht op droogtijd na regen, beregening of herstart",
        ),
        (
            lambda n, r: {
                "lawn_mower.eufy_robomow_e15_mower": (
                    "docked",
                    {
                        "telemetry_updated_at": (n - timedelta(minutes=2)).isoformat(),
                        "operating_mode": "control",
                    },
                )
            },
            "Maaierstatus ontbreekt of is verouderd",
        ),
        (
            lambda n, r: {
                "input_boolean.example_irrigation_planned": ("on", {}),
                "input_datetime.example_irrigation_start": (
                    (n + timedelta(minutes=30)).isoformat(),
                    {},
                ),
            },
            "Beregening gepland binnen de maaisessie",
        ),
        (
            lambda n, r: {"switch.eufy_robomow_e15_child_protection": ("off", {})},
            "Regenstop of kinderbeveiliging niet bevestigd",
        ),
    ],
)
def test_unsafe_or_ambiguous_inputs_block_start(tmp_path, overrides, expected):
    assert asyncio.run(render_scenario(tmp_path, overrides))[1] == expected


def test_actual_script_cannot_start_when_automatic_mode_is_off(tmp_path):
    """Execute the real script in isolated HA with fake services and no device."""
    from homeassistant.core import Context
    from homeassistant.helpers.script import Script

    async def run():
        hass = HomeAssistant(str(tmp_path))
        calls = []

        async def record(call):
            calls.append(call)

        for domain, service in (
            ("lawn_mower", "start_mowing"),
            ("input_datetime", "set_datetime"),
            ("input_boolean", "turn_on"),
        ):
            hass.services.async_register(domain, service, record)
        hass.states.async_set("input_boolean.eufy_automatisch_maaien", "off")
        # Even an outdated ready sensor must not bypass the explicit off guard.
        hass.states.async_set("sensor.eufy_maaiplanning", "Klaar om te maaien")
        script = Script(
            hass, PACKAGE["script"]["eufy_gepland_maaien"]["sequence"], "Mower guard test", "test"
        )
        await script.async_run(context=Context())
        assert calls == []
        await hass.async_stop()

    asyncio.run(run())
