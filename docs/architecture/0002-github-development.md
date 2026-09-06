# ADR 0002: Continue development on GitHub

Status: accepted by the repository owner, 2026-09-06.

`keesmod/eufy-robomow-ha` is the development source of truth. Use GitHub issues,
pull requests and Actions for ongoing work. Port the dashboard, confirmed command
handling, session history and optional planning package onto the existing GitHub
history. Preserve the public map-source API and its authentication and config
options; do not replace it with the private deployment's helper interface.

The earlier GitLab checkout remains local reference material. Do not merge its
private history, household configuration or raw observations into this public
repository. The private P2P runtime and vendor binaries remain outside this
publication. Existing attribution and licensing status remain unchanged; this
migration is not a release or a new license grant.

CI runs on hosted GitHub runners with read-only repository permissions. It covers
Python quality/types/tests, frontend tests, Hassfest, runtime dependency audit
and source secret scanning. CI never deploys to Home Assistant or operates a
mower. Secret-scan exceptions are limited to exact inherited application constants
and exact synthetic test values in their respective files.

The supervised source implementation was validated on an E15 with start, pause
and dock telemetry plus user observation of the physical outcomes. That evidence
does not establish zone commands, resume-specific behavior or a naturally timed
automatic session. The public port requires its own automated checks; it does
not imply that this public build has been deployed to the private installation.
