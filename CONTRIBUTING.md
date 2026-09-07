# Contributing

This integration is under private-alpha development even though its working fork is visible on GitHub.

## Development workflow

1. Create a focused branch from `main`.
2. Use conventional commit messages.
3. Keep protocol parsing separate from Home Assistant framework glue where practical.
4. Run `python -m ruff check .` and `python -m pytest` with Python 3.14.
5. Describe scope, validation, risk, and rollback in the pull request.

## Protocol provenance

Only submit behavior and constants independently reproduced on owned hardware or derived from documentation that permits reuse. Other unlicensed mower integrations may inform a feature checklist, but their source, fixtures, constants, schemas, and tests must not be copied.

Every protocol change must identify its evidence in the pull request. Repeat physical observations three times before marking a field or command confirmed.

## Safety and privacy

Never include passwords, access or refresh tokens, local keys, complete device identifiers, account identifiers, exact coordinates, private lawn geometry, or raw captures. Use synthetic or redacted fixtures.

Physical commands require daylight, a clear lawn, continuous supervision, and known physical stop controls. Do not bypass mower safety interlocks.

## Licensing

The inherited upstream history currently declares no license. Do not add a repository-wide license, mirror this source to another forge, or publish a release under a new license without resolving that boundary.
