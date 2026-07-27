# Eufy Robomow — Home Assistant Integration

A Home Assistant custom integration for the **Eufy E15** robotic lawn mower. The design is capability-driven for possible E18 support, but E18 support is not yet hardware-validated or claimed.

Control and monitor your Eufy mower directly from Home Assistant over your local network, with cloud-synced settings pulled straight from your Eufy account — no extra tools or manual key extraction required.

---

## Features

| Entity | Type | Description |
|--------|------|-------------|
| Mower | `lawn_mower` | Activity state; start, pause, and dock after control is explicitly enabled |
| Battery | `sensor` | Battery level (%) |
| Mowed Area | `sensor` | Area covered in the current or last session |
| Mowing Progress | `sensor` | Real-time session completion % (from DP113 telemetry blob) |
| Return Progress | `sensor` | Return-to-base progress (%) |
| Session Distance | `sensor` | Distance traveled in the current session (m) |
| Network | `sensor` | WiFi / Cellular connection type |
| Signal Strength | `sensor` | WiFi signal strength (dBm) |
| Cut Height | `number` | Blade height 25–75 mm, step 5 mm (local, instant) |
| Volume | `number` | Speaker volume 0–100 % (local) |
| Edge Distance | `number` | −15 to +15 cm — how far inside/outside the border wire the mower cuts |
| Pad Direction | `number` | Mowing path angle 0–359° |
| Travel Speed | `select` | Mower driving speed: slow / normal / fast |
| Blade Speed | `select` | Blade motor speed: slow / normal / fast |
| Path Distance | `select` | Lane spacing: 8 cm / 10 cm / 12 cm |
| Stop on Rain | `switch` | Pause mowing when rain is detected |
| Child Protection | `switch` | Enable child/pet protection mode |
| Smart No-Go Suggestions | `switch` | AI-assisted no-go zone suggestions |
| Mow Yellow Grass | `switch` | Allow mowing on dry/yellow grass |

> **Cloud entities** (edge distance, pad direction, speeds, path distance) require your Eufy account credentials. They are polled every 5 minutes and written back via the Tuya mobile API.
>
> Some entities (generic raw DP sensors, map coverage) are **disabled by default** — enable them in HA if you want to explore unconfirmed data points.

---

## Prerequisites

- **Local network access** — the mower and Home Assistant must be on the same LAN (or the mower reachable via IP).
- **Eufy account** — required for cloud-managed settings. The same email/password you use in the Eufy Home app.
- Home Assistant **2026.7** or newer during private alpha development.

---

## Installation

### Private development installation

1. Copy the `custom_components/eufy_robomow/` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant

HACS packaging is intentionally deferred until the private integration has passed protocol, safety, and reliability validation.

---

## Configuration

Go to **Settings → Devices & Services → Add Integration → Eufy Robomow**.

**Step 1 — Sign in:**
Enter your Eufy account email and password. The integration will automatically discover all your devices and fetch their local keys.

**Step 2 — Select mower:**
Pick your mower from the dropdown and enter its local IP address (find it in your router's DHCP table or the Eufy app's device info screen).

That's it — no external tools, no manual key extraction.

New entries start in **observe-only** mode. In this mode the mower entity reports state, but physical commands and settings-write entities are disabled. After supervised read-only validation, use **Settings → Devices & Services → Eufy Robomow → Configure** to opt in to control.

> **Alpha credential notice:** the current cloud client still stores the Eufy account password in the Home Assistant config entry so it can renew sessions. Restrict access to Home Assistant backups and `.storage`; replacing this with renewable session material is tracked as a separate hardening change.

---

## How it works

- **Local polling** (every 10 s) via the [Tuya local protocol](https://github.com/jasonacox/tinytuya) for real-time status (battery, activity state, etc.).
- **Cloud polling** (every 5 min) via the Tuya mobile API for settings stored as protobuf blobs in DP155.
- **Writes**, when control is explicitly enabled, go to either the local mower or the cloud API depending on the setting.

---

## Known limitations

- **Zone mowing** — the local Tuya protocol cannot distinguish zone 1 from zone 2 (both send an identical DP154 value; zone selection happens over cloud MQTT which is not locally accessible). Full-area mow only for now.
- **Map display** — live GPS map is not yet supported.

---

## Troubleshooting

- **Entities unavailable** — check that the IP address is correct and the mower is on WiFi (not cellular only).
- **Cloud settings not updating** — cloud data refreshes every 5 minutes; changes made in the Eufy app will appear after the next refresh cycle.
- Enable **debug logging** for detailed output:

```yaml
# configuration.yaml
logger:
  logs:
    custom_components.eufy_robomow: debug
```

---

## Credits

Authentication and local-key discovery based on [eufy-clean-local-key-grabber](https://github.com/albaintor/eufy-clean-local-key-grabber).
Local protocol via [tinytuya](https://github.com/jasonacox/tinytuya).
