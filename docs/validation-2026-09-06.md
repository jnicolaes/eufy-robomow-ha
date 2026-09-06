# Home Assistant validation — 2026-09-06

The GitHub implementation was installed on HA Core 2026.9.0 for a supervised E15
test. All 24 deployed integration/resource files initially matched GitHub main
at `3b0d8f8`. The earlier private installation was backed up before replacement;
its map URL and certificate pin were migrated to the public option names with
the existing derived authentication. Entity identities, dashboard, settings and
session storage were preserved. No account details or private geometry are
included in this record. App and firmware versions were not refreshed, so this
does not establish behavior across firmware versions.

With the owner present, the lawn clear, irrigation off, daylight and onboard
rain/child protection enabled, the following standard HA service calls each
returned HTTP 200 and were confirmed by fresh local device telemetry (UTC):

| Action | Requested | Confirmed | Evidence |
| --- | --- | --- | --- |
| Start | 17:05:49 | 17:05:51 | Mowing reported |
| Pause | 17:05:54 | 17:06:00 | Pause reported |
| Resume | 17:06:03 | 17:06:04 | Mowing reported |
| Pause | 17:06:07 | 17:06:14 | Pause reported |
| Return | 17:06:17 | 17:06:19 | Task inactive |

The owner separately confirmed observing the complete sequence and physical
arrival at the charging station. The inactive-task flag alone does not prove
arrival. No recovery fallback was needed. The session was persisted with two
pauses, approximately 19 seconds observed mowing and nine seconds paused, with
both session edges observed and no telemetry gap. Both stored sessions survived
the subsequent restart.

Live validation uncovered that `ImageEntity` defaults to no polling. The map
loaded at setup but was not subsequently refreshed despite a configured polling
interval. Explicitly enabling entity polling fixes the responsible surface.
The regression test exercises Home Assistant's actual platform polling filter:
it fails without the correction and passes with it, including the streaming-to-
idle transition. The corrected installation's automatic reports advance without
manual update calls or mower movement. Source fetches remain throttled to the
existing five-minute idle/two-second active cadence.

The browser loaded the actual map, zoomed to 150%, displayed current setting
controls and both history rows; zoom was restored afterward. Both live
configuration checks passed. The checked Eufy logs contained only the standard
custom-integration loader warnings. Local validation passed 86 Python tests,
Ruff and mypy; the four frontend tests had already passed for the unchanged card.

Automatic mowing remains off. A naturally timed automatic session and zone/box/
spot commands remain unvalidated. This test does not claim new zone support or
complete unattended-operation validation.
