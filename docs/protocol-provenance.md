# Protocol provenance policy

Protocol knowledge enters this repository only through independently reproducible evidence.

## Accepted evidence

- Official Eufy documentation and user-visible app behavior.
- Redacted observations from an owned E15: before/action/after state, timestamps, screenshots, packet metadata, and resulting mower or app state.
- Synthetic fixtures derived from a confirmed field definition.
- A third-party implementation only when its license and provenance explicitly permit reuse.

## Confirmation levels

- `hypothesis`: a plausible interpretation with insufficient evidence.
- `observed`: seen once or twice and not yet safe to encode as a stable contract.
- `confirmed`: independently reproduced at least three times on the recorded app and firmware versions.
- `hardware-validated`: confirmed through supervised physical behavior, not only transport acknowledgement.

Only confirmed facts belong in production constants and parsers. Physical commands additionally require hardware validation and rollback instructions.

## Required capture context

Record the Eufy app version, mower model, mower firmware, timezone, operating mode, and the exact user action for every series. Keep speculative observations in tracker issues until confirmed.

## Redaction

Never commit raw captures. Remove passwords, tokens, local keys, full device and account identifiers, serials, ICCIDs, MAC addresses, IP addresses, exact coordinates, and private lawn geometry. Replace stable values with documented synthetic placeholders so tests preserve structure without preserving identity.

## Other repositories

Unlicensed projects and forks may be used to enumerate features and design experiments. Do not copy or mechanically translate their implementation, constants, schemas, fixtures, or tests. Record independently reproduced facts in this repository's own evidence trail.

## Current map-position interpretation

The E15 map record's field 8 and the latest clean-path position have been
repeatedly observed against the iOS app, but their dual live-session semantics
are not documented by Eufy or Tuya. The renderer therefore treats:

- map-record field 8 as the idle mower pose;
- the latest clean-path position as the live mower pose while mowing; and
- map-record field 8 as the charging-station marker during that live view.

This is an `observed` presentation rule, not a confirmed protocol contract. The
fixed SVG marker offset is visual alignment for the renderer's fixed-size icons
and is clamped to the canvas. None of these positions may be used for commands,
safety decisions, virtual-fence writes, or other physical control.
