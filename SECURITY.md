# Security policy

## Current status

This repository is an alpha integration for a physical device. It is not ready for unattended mower control, HACS distribution, or public support claims.

## Reporting a vulnerability

Do not post secrets, private captures, device identifiers, coordinates, or lawn geometry in a public issue. Contact the repository owner privately with a minimal redacted reproduction. If no private channel is available, open an issue containing no sensitive data and ask for a private contact method.

## Sensitive data

Home Assistant config entries currently contain the mower local key and, when cloud support is enabled, the Eufy email and password. Protect Home Assistant `.storage` and backups accordingly. A future credential-lifecycle change must avoid retaining the account password once renewable session material is proven sufficient.

Logs and diagnostics must redact credentials, full identifiers, coordinates, geometry, and raw payload values. Debug logging is not permission to emit sensitive data.

## Physical safety

The integration defaults to `observe_only`. Enable `control` only for supervised testing. Never use this integration to bypass child lock, rain, obstacle, blade, firmware, or other mower safety mechanisms.

Manual driving, map mutation, camera access, firmware updates, reset, deletion, and account administration require separate security reviews and are out of scope for the current release line.
