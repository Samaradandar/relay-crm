# ADR 0004: Permissions are data

Status: accepted (Phase 1).

`permission_keys` and `role_permissions` hold the PRD matrix (sections A-L). Code only calls
`guard` with a key; there are no `if role == ...` checks. `app/platform/permissions/matrix.py`
is the reviewed source and `python -m app.platform.permissions.sync` upserts it. Two PRD rows
that mix classes are split in two keys (`criterion.verify`/`criterion.reject`,
`password.reset_own`/`password.reset_others`). CI fails if a key lacks a class or a role row.
