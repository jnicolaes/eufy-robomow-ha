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

Implementation requires a stable area identifier, its relationship to the active
map generation, a validated command and device acceptance/error semantics. Its
tests must cover stale map IDs, removed/unknown areas, unavailable telemetry and
rejected requests. Keep whole-lawn start separate. Confirm the physical destination
under supervision before labelling area control physically validated.
