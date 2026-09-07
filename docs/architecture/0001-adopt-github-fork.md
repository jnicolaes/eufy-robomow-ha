# ADR 0001: Adopt the existing GitHub fork as the functional base

Status: accepted (private development)

## Decision

Development continues from `keesmod/eufy-robomow-ha`, which currently preserves the history of `jnicolaes/eufy-robomow-ha`. The separate private GitLab project remains the tracker and protocol-evidence repository; its existing history is not rewritten.

The integration defaults to `observe_only`. Physical commands and setting writes require an explicit switch to `control` in Home Assistant's integration options.

## Rationale

The adopted code already implements Eufy login, local-key discovery, TinyTuya 3.5 polling, mower controls, telemetry, and several settings. Building those layers again would add risk and delay. Safety controls, typed boundaries, verification, and missing app features can be added incrementally around the existing behavior.

## Provenance and licensing boundary

The upstream repository does not currently declare a software license. GitHub's fork mechanism is used as the development base, but source is not mirrored into the GitLab repository or redistributed under another license. Contributions added here must have clear provenance. Publication and relicensing require a separate licensing decision.

Other public forks may be used only as feature and behavior checklists. Their implementation details are not copied unless their licensing and provenance permit it.

## Consequences

- The first delivery slice hardens existing behavior instead of recreating it.
- HACS publication remains deferred.
- Credential lifecycle, command acknowledgement, state normalization, schedules, zones, and maps remain separate reviewable slices.
- Rollback is a branch reset/removal; no live system is changed by this decision.
