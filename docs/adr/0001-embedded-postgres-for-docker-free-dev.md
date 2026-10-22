# ADR 0001: Tests and local runs use an embedded PostgreSQL when Docker is not available

Status: accepted (Phase 1).

Context: the team needs to run the whole backend and its tests on laptops without Docker/WSL.
Decision: tests start PostgreSQL 16 through the `pgserver` package (real Postgres, never SQLite);
`api/scripts/run_local.py` does the same for development. CI and compose still use `postgres:16`
(set `TEST_ADMIN_URL` to reuse any server).
Consequence: the embedded build has no contrib extensions, so Phase 1 does not use `citext`
(emails are unique through `lower(email)`). `pg_trgm` is needed for fuzzy search in Phase 2;
the compose Postgres has it, and Phase 2 must check for it before relying on it.
