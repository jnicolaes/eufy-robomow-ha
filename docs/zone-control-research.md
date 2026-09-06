# Zone mowing research — 2026-09-06

The current integration has no validated zone command. Its private map helper
only acquires maps; map geometry must not be treated as command support. The
inherited source contains conflicting comments about DP154 (unknown purpose,
old direction hypothesis, zone-mode hypothesis). None proves an E15 zone payload.

Eufy's [E15/E18 multi-lawn support article](https://service.eufy.com/article-description/How-many-maps-can-the-E15-and-E18-Robot-Lawn-Mowers-support-Are-they-capable-of-working-on-multiple-lawns),
checked 2026-09-06, confirms one map per base station with multiple lawns connected
through Multi-Zone Pathway. This establishes multi-lawn mapping as a supported
product feature. It does not supply area command identifiers, a transport schema,
or evidence of which selectable areas exist on the owned mower. Multiple lawns,
must-mow geometry and no-go geometry must remain distinct in this investigation.

Tuya's [boundaryless lawn mower protocol documentation](https://developer.tuya.com/en/docs/iot-device-dev/rlm_mqtt_ble_func?id=Kfcwmf6m68j0v),
updated 2026-06-29, describes a separate cloud-to-device mowing task interface,
including area mowing. It also documents schedule and mowing-history interfaces.
This is a credible research direction, not evidence that this E15 firmware uses
that protocol or that its identifiers match the private map renderer.

The private deployment's E15 decoder explicitly returns an empty `zones` collection. It
decodes map-record field 26 as base areas, consistent with the earlier app/map
comparison; these must not be relabelled as selectable mowing zones. The
normalized map model retains a map identifier and geometry but has no validated
area identifiers or names. Zone selection therefore requires evidence for both
the command transport and the mapping of selectable areas to device identifiers.

The helper's MQTT decoder accepts only protocol 302 P2P signaling and discards
other protocols. It is not currently a zone-command observer, and the presence
of an incoming subscription does not prove that app-originated commands are
visible there. Keep this signaling boundary intact until the relevant transport
is established. First confirm whether the installed Eufy app offers individual
area selection and whether multiple areas exist; a paired comparison is not
possible from the currently decoded map alone.

The next evidence needed is a paired, supervised Eufy-app action for two known
areas: record the transport and redacted structural differences, correlate the
chosen area with the accepted task and actual mower destination, and establish
map-generation binding. First compare read-only observations; do not guess writes
from vacuum protocols, DP154 comments or SDK enum names. Retain app/firmware
versions alongside the observation and keep raw captures on the private host.

Only after proving that mapping should an area-select action be exposed. It must
reject missing or stale map/area identifiers and must never fall back silently
to mowing the entire lawn. Map editing, fence changes and remote driving remain
outside this feature.

## Next observation and acceptance evidence

The user supplied an app screenshot on 2026-09-06 showing the four modes Entire,
Zone, Box and Spot. Zone is selected and one numbered area is visible. This
confirms an area-selection UI on the owned device; a visible label is not yet a
device area identifier. The screenshot does not establish the total configured
area count, app/firmware versions or command payload. Private map geometry and
the screenshot itself are not stored in the repository.

At 16:31:59 UTC the existing HA telemetry reported the mower idle. A separate,
read-only cloud sample at 16:33:35 UTC contained 86 DPs. DP154 decoded as a
two-byte protobuf containing an integer at field 3. That shape alone neither
establishes a zone mode nor an area ID. A temporary baseline containing DPS only
was saved mode 0600 in the Core container's `/tmp`; no credentials, raw DPS or
geometry were copied to the workstation or repository. No mower command was
sent. The screenshot and sample are not a synchronized mode-change comparison.

The next requested app action is selecting Entire without pressing Start, then
comparing the read-only cloud snapshot. Selection-only changes may be local to
the app until Start is pressed; unchanged cloud data would leave the command
mapping unresolved. One visible zone is enough for an initial Entire-versus-Zone
comparison; validation across two area IDs remains conditional on a second
existing selectable area.

Before collecting a physical app-action comparison, confirm current app/firmware
versions and the existing area inventory. Do not create or split
areas merely to manufacture a test case: that would change the lawn map. If
individual selection is unavailable, record that limitation rather than inventing
an unsupported HA control.

For an existing selector, distinguish selecting an area from starting its task.
First inspect selection-only changes without sending HA commands. For each later
supervised task, retain the initial state, selected area's private alias, request
time, changed payload structure and device task response; repeat with a different
existing area. Use host-private captures and publish only redacted structure.
Local activity flags alone cannot establish which area was selected. A usable
mapping needs both the app-selected area and matching device/task evidence.

## Confirmed selection-only comparison

On 2026-09-06 the owner confirmed selecting Entire without Start, followed by
Zone and the visible area labelled 1, again without Start. Read-only cloud
samples were acquired at 17:24:28.976761 UTC and 17:25:24.836711 UTC respectively.
Both contained 86 DPs; HA reported the mower docked at each sample. Raw snapshots
remain mode 0600 in the Core container's temporary directory, outside this repo.

Exactly DP113, DP154 and DP155 differed, with no added or removed DP keys:

- DP154 changed from a one-byte null sentinel to protobuf field 3, wire type 0,
  integer 1. This is evidence of a selection-associated change. It does not yet
  distinguish a Zone mode enum from an area identifier or establish a command.
- DP155 retained the same five decoded settings. Its top-level field 7 (integer
  80 in Entire) was absent in the Zone sample; the remaining parsed structure
  matched. Do not interpret the omitted redundant path-distance field as an
  area selector.
- DP113 changed from fields 1 through 5 to field 1 only. This may reflect session
  telemetry being cleared, but two snapshots do not establish its cause.

At 17:27:45.553398 UTC, after the owner confirmed returning to Entire without
Start, a third sample matched the original Entire sample exactly across all 86
DPs. DP154 returned to the null sentinel and DP155 also returned to its original
representation. HA again reported docked. This Entire–Zone 1–Entire comparison
establishes a reversible association with the app selection; it still does not
distinguish a mode enum from a selected-area identifier.

At 17:29:26.936874 UTC, after the owner confirmed selecting Box without drawing
a box or pressing Start, DP154 contained field 3, wire type 0, integer 2. Only
DP113 and DP154 differed from the original Entire sample; all 86 DP keys remained
present and DP155 matched Entire exactly. HA reported docked. The association
Entire=null, Zone=1, Box=2 supports a mowing-mode interpretation of field 3,
rather than treating its Zone value as the visible area's identifier. No box
geometry was selected or captured in this test.

The subsequent Spot selection attempt was rejected by the app with the owner's
reported message, "spot is only available when the robot is outside the base
station". A read-only sample at 17:30:46.196447 UTC matched the Box sample across
all 86 DPs, including DP154 field 3 = 2; HA still reported docked. This proves no
observed state change for the rejected attempt, not a Spot enum value. Do not
assign Spot=3 by extrapolation or bypass the app's station restriction.

Further area comparisons and supervised task acceptance are still required
before adding a zone write. Confirm the number of existing selectable zones
before choosing that comparison. No integration command or setting write was
issued for these samples.

The owner subsequently confirmed that only one area exists, labelled 1, and
returned the app to Entire. A comparison between two distinct existing areas is
therefore unavailable on this map. Do not create another area for testing or
claim multi-area targeting from a single-area start. The next feasible evidence
is observing a short app-originated Zone 1 task, with the existing HA return/pause
path bounding its duration; this can establish mode/task association but may
still leave the area identifier and command transport unresolved.

## Supervised app-originated Zone 1 task

The owner explicitly approved the short zone test with automatic HA return and
pause as its recovery fallback. The first observer exited before arming because
the app was already on Zone instead of Entire. The next observer accepted the
observed Zone starting state and armed at 17:36:17.061131 UTC after checking idle
state, fresh local telemetry, manual control mode, automatic planning off,
irrigation off, daylight, sufficient battery and enabled rain/child protection.

The owner pressed Start in the Eufy app with Zone 1 selected. HA reported mowing
at 17:36:59.494308 UTC from fresh local telemetry dated 17:36:58.982397 UTC. A cloud
sample at 17:36:59.541848 UTC retained DP154 field 3 = 1. Compared with the Zone
selection-only sample, exactly DP1, DP103, DP107, DP113, DP143 and DP152 changed.
DP1 became true; DP152 gained top-level integer field 1 = 1 while its two nested
messages remained unchanged. These task-associated changes do not establish an
area identifier. The observer read status; it did not capture the app's outbound
request or establish which other fields the app sent.

Twenty seconds after detecting activity, the observer requested the existing HA
dock action. Its HTTP 200 response and fresh task-inactive confirmation were
recorded at 17:37:30.996201 UTC. No pause fallback was needed, and the observer
exited successfully. The owner separately confirmed physical departure and
arrival back at the station. Task inactive alone is not evidence of arrival.

This validates an app-originated task in the observed Zone mode on the owner's
single-area map and the HA return path. It does not yet validate an HA zone write,
multi-area targeting, map/area identity binding or the Spot mode. Raw status
captures remain private on the HA host; no credentials or lawn geometry are
included here.

## Area-identity investigation after the app task

The owned device's cloud inventory response contains no product/DP schema. The
cached raw map does contain one previously unused record at map-record field
10, subfield 4. Its subfield 3 polygon exactly matches the complete boundary in
field 10, subfield 3. Integer metadata subfields 4 and 6 both equal 1. These are
candidate metadata fields, not established area IDs: neither has yet been varied
or matched to an outbound task request. Map-record field 23 also contains nested
metadata with a one-byte zero payload; its meaning is unknown. Do not assume UI
label 1 maps to either a value of 1 or a zero-based value of 0.

A fresh cloud baseline at 17:47:03.210187 UTC still reported Zone mode and a docked
mower. The next non-motion comparison is deselecting the sole existing area while
remaining in Zone mode. This isolates area selection from the already observed
mode changes if the app permits an empty selection and transmits it. Cached map
geometry and raw DP snapshots remain on the private HA host.

At 17:49:08.341467 UTC, after the owner confirmed deselecting area 1 while staying
in Zone, DP154 and DP155 matched the selected baseline. Of 86 DPs, only DP107 and
DP152 differed. DP152 retained its nested fields 3 and 4 and gained top-level
integer field 5 = 1. DP107 changed from a one-byte opaque value to integer field
4 = 2. HA reported docked. This is an observed difference after deselection, not
proof that either field contains an area identifier; background status changes
remain possible. Re-selecting area 1 is the next comparison to test reversibility.

At 17:51:15.049781 UTC, after the owner confirmed reselecting area 1, all 86 DPs
matched the deselected sample. DP107 and DP152 did not revert to the initial
selected baseline. The selected–deselected–selected comparison therefore does
not establish either changed field as an area selector. Further identical
status-polling/click cycles have no demonstrated information gain. The next
evidence path is an app-originated request or independently identified area
schema; an incoming MQTT subscription alone does not prove request visibility.

A bounded passive observer was subsequently attached to the existing helper's
receive callback. It retained the original relay filtering, subscriptions and
publish behavior. The sampled decrypted traffic contained 21 protocol-302 P2P
signaling messages and no DP fields. No user-confirmed app selection was received
during that observation window, so this does not establish whether an app command
would be visible on the connection. Payloads outside the existing device-key
decoder were not inspected; absence of a decoded message is not proof of absence
of traffic. Repeating status polling is still insufficient for area identity.

The temporary observer and startup override were removed, the original relay
source checksum remained unchanged, and normal idle map requests were restored.
The authenticated helper status returned HTTP 200, healthy, zero consecutive
failures and a fresh success timestamp after cleanup. No mower actuation occurred
in this passive investigation.

Implementation requires a stable area identifier, its relationship to the active
map generation, a validated command and device acceptance/error semantics. Its
tests must cover stale map IDs, removed/unknown areas, unavailable telemetry and
rejected requests. Keep whole-lawn start separate. Confirm the physical destination
under supervision before labelling area control physically validated.
