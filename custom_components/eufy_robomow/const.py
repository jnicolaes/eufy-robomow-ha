"""Constants for the Eufy Robomow integration."""

DOMAIN = "eufy_robomow"

# Tuya local protocol
DEFAULT_PORT = 6668
TUYA_VERSION = 3.5
POLL_INTERVAL = 10  # seconds — reduced for faster reverse engineering

# Generic sensor prefix for unmapped DPS
GENERIC_SENSOR_PREFIX = "dp"

# Speed options (used by select.py)
SPEED_SLOW = "slow"
SPEED_NORMAL = "normal"
SPEED_FAST = "fast"
SPEED_OPTIONS = [SPEED_SLOW, SPEED_NORMAL, SPEED_FAST]

# Config entry keys
CONF_DEVICE_ID = "device_id"
CONF_LOCAL_KEY = "local_key"
CONF_EUFY_EMAIL = "eufy_email"  # optional — enables cloud settings
CONF_EUFY_PASSWORD = "eufy_password"  # optional — enables cloud settings
CONF_OPERATING_MODE = "operating_mode"

# Physical commands and setting writes are opt-in while the integration is alpha.
OPERATING_MODE_OBSERVE_ONLY = "observe_only"
OPERATING_MODE_CONTROL = "control"
OPERATING_MODES = [OPERATING_MODE_OBSERVE_ONLY, OPERATING_MODE_CONTROL]
DEFAULT_OPERATING_MODE = OPERATING_MODE_OBSERVE_ONLY

# ── Cloud settings poll interval ───────────────────────────────────────────────
# Cloud settings are fetched at most once every N seconds (much slower than local).
# On repeated failures, the coordinator applies exponential backoff up to the cap.
CLOUD_POLL_INTERVAL = 300   # 5 minutes (base retry interval)
_CLOUD_MAX_BACKOFF  = 3600  # 1 hour   (maximum retry interval after repeated failures)

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
# NOTE: DP154 is the zone-mow-mode signal, NOT the direction DP.
PAD_DIRECTION_MIN = 0  # degrees (west / 9 o'clock)
PAD_DIRECTION_MAX = 359  # degrees (full rotation)
PAD_DIRECTION_STEP = 1  # degrees

# ── DPS READ (status) ──────────────────────────────────────────────────────────
# Confirmed via live monitoring of Eufy E15 (Tuya v3.5)

DP_TASK_ACTIVE = "1"  # bool  True = a mowing/returning session is running
DP_PAUSED = "2"  # bool  True = session paused, False = actively moving
DP_BATTERY = "8"  # int   Battery level 0–100 %
DP_VOLUME = "26"  # int   Speaker volume 0–100 %
DP_CHILD_PROTECTION = "47"  # bool  True = child/pet protection active
DP_SIGNAL = "109"  # int   Signal strength (raw value; e.g. 50 → −50 dBm)
DP_CUT_HEIGHT = "110"  # int   Blade height in mm (e.g. 40)
DP_LIVE_VIEW = "114"  # int   Live-view/camera state:
#       32  = idle (no live view)
#       103 = camera enabled (user opened live view)
#       104 = camera disabled (user closed live view)
DP_PROGRESS = "118"  # int   0–100 % progress of current action
#       0   = idle / mowing
#       1-99 = saving map or returning to base
#       100 = docked / fully done
DP_TOTAL_TIME = "125"  # int   Total mow time — ~6.6 sec/unit
#       36149 units ≈ 66h (app: 2d 18h) ✓
DP_AREA = "126"  # int   Mowed area counter (exact unit unconfirmed)
DP_RAIN_DETECTION = "101"  # bool  True = stop mowing when rain detected (writable setting)
DP_SMART_SUGGESTION = "132"  # bool  Smart suggestion for no-go zones
DP_REAL_LAWN_MAP = "133"  # bool  Real lawn map feature enabled
DP_NETWORK = "134"  # str   "Wifi" or "Cellular"
DP_MOW_YELLOW_GRASS = "141"  # bool  Allow mowing on yellow/dry grass

# ── Unmapped DPs for reverse engineering (all DPS exposed as sensors) ─────────
# These are automatically discovered and added as generic sensors.

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
#  DP1=True,    DP2=False,  DP118=100         → MOWING  (briefly docked mid-session
#                                               for charging; DP1 still True so we
#                                               report MOWING not DOCKED)
#  DP1 absent/False                           → DOCKED  (no active session)
#  DP1=True,    DP2=True                      → PAUSED
#
RETURNING_THRESHOLD = 5  # DP118 ≥ this value while DP1 active = RETURNING
