"""Constants for the Eufy Robomow integration."""

DOMAIN = "eufy_robomow"

# Tuya local protocol
DEFAULT_PORT = 6668
TUYA_VERSION = 3.5
POLL_INTERVAL = 30  # seconds — 10 s was too aggressive; caused thread/FD pressure

# Config entry keys
CONF_DEVICE_ID = "device_id"
CONF_LOCAL_KEY = "local_key"
CONF_EUFY_EMAIL = "eufy_email"  # optional — enables cloud settings
CONF_EUFY_PASSWORD = "eufy_password"  # optional — enables cloud settings

# ── Cloud settings poll interval ───────────────────────────────────────────────
# Cloud settings are fetched at most once every N seconds (much slower than local).
CLOUD_POLL_INTERVAL = 300  # 5 minutes

# ── Cloud settings data keys (stored in coordinator.data) ─────────────────────
# These keys coexist with numeric DPS keys; the "cloud_" prefix avoids collisions.
CLOUD_EDGE_MM = "cloud_edge_mm"
CLOUD_PATH_MM = "cloud_path_mm"
CLOUD_TRAVEL_SPEED = "cloud_travel_speed"
CLOUD_BLADE_SPEED = "cloud_blade_speed"
CLOUD_PAD_DIRECTION = "cloud_pad_direction"

# ── Edge distance (cm) ────────────────────────────────────────────────────────
# Matches app range: -15 to +15 cm, step 1 cm.
# Negative = mower cuts slightly beyond the border wire;
# positive = mower stays inside.  Stored as mm in DP155 field 3.
# NOTE: negative values use protobuf int32 encoding (10-byte varint).
# If the mower rejects negative values the field may use sint32 (zigzag);
# see cloud.py _varint_encode for the switch.
EDGE_DISTANCE_MIN = -15  # cm
EDGE_DISTANCE_MAX = 15  # cm
EDGE_DISTANCE_STEP = 1  # cm

# ── Path distance — app has exactly 3 options: 8 / 10 / 12 cm ────────────────
# Stored as mm in DP155 field 5.  Exposed as a SelectEntity.
PATH_DISTANCE_OPTIONS: list[str] = ["8 cm", "10 cm", "12 cm"]
PATH_DISTANCE_MM: dict[str, int] = {"8 cm": 80, "10 cm": 100, "12 cm": 120}

# ── Pad direction (mowing path angle) — DP155 field 4 ─────────────────────────
# Stored as an integer in DP155 field 4, sub-field 2, inner field 1.
# Scale: 1 unit = 1 degree.  Reference direction: 0 = west (9 o'clock position).
# Confirmed live data points:
#   12 o'clock (north) ≈ 90–91
#   3  o'clock (east)  ≈ 178–180
# DP154 was previously assumed to hold this, but live testing showed it does NOT
# change when the app's direction dial is moved.  DP154's purpose is unknown.
PAD_DIRECTION_MIN = 0    # degrees (west / 9 o'clock)
PAD_DIRECTION_MAX = 359  # degrees (full rotation)
PAD_DIRECTION_STEP = 1   # degrees

# ── DPS READ (status) ──────────────────────────────────────────────────────────
# Confirmed via live monitoring of Eufy E15 (Tuya v3.5)

DP_TASK_ACTIVE = "1"  # bool  True = a mowing/returning session is running
DP_PAUSED = "2"  # bool  True = session paused, False = actively moving
DP_BATTERY = "8"  # int   Battery level 0–100 %
DP_CUT_HEIGHT = "110"  # int   Blade height in mm (e.g. 40)
DP_PROGRESS = "118"  # int   0–100 % progress of current action
#       0   = idle / mowing
#       1-99 = saving map or returning to base
#       100 = docked / fully done
DP_AREA = "126"  # int   Mowed area counter (exact unit unconfirmed)
DP_TOTAL_TIME = "125"  # int   Total mow time — ~6.6 sec/unit
#       36149 units ≈ 66h (app: 2d 18h) ✓
DP_NETWORK = "134"  # str   "Wifi" or "Cellular"

# ── DPS WRITE (commands) ──────────────────────────────────────────────────────
# NOTE: These have NOT yet been confirmed by writing locally.
# They are the most likely candidates based on observed state changes.
# Test with: tinytuya Device.set_value(dp, value)

CMD_START = ("1", True)  # Set DP 1 = True  → start mowing
CMD_PAUSE = ("2", True)  # Set DP 2 = True  → pause session
CMD_RESUME = ("2", False)  # Set DP 2 = False → resume paused session
CMD_DOCK = ("1", False)  # Set DP 1 = False → stop & return to base
# (fallback: may need a dedicated dock DP)

# ── Cut height ────────────────────────────────────────────────────────────────
CUT_HEIGHT_MIN = 25  # mm (confirmed app minimum)
CUT_HEIGHT_MAX = 75  # mm (confirmed app maximum)
CUT_HEIGHT_STEP = 5  # mm

# ── Activity state logic ──────────────────────────────────────────────────────
# Confirmed via live DPS monitoring:
#
#  DP1 absent,  DP2 absent                    → DOCKED  (cold / never started)
#  DP1=True,    DP2=False,  DP118=0           → MOWING
#  DP1=True,    DP2=False,  DP118 5–99        → RETURNING (progress back to base)
#  DP1=True,    DP2=False,  DP118=100         → DOCKED  (returned after session)
#  DP1=True,    DP2=True                      → PAUSED
#
RETURNING_THRESHOLD = 5  # DP118 ≥ this value while DP1 active = RETURNING
