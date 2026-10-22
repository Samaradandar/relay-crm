# Relay CRM

Internal CRM for the sales team. Spec: `docs/prd/` (PRD v3.6). Plan: Build Phases PDF.
Built: **Phase 1** (login, users and teams, four roles with Own/Team/All scope, reason rules, audit
trail, outbox and jobs, notifications, approvals, live updates, files), **Phase 2** (new lead with
duplicate check, Lead Pool, assignment rules and caps, transfers) and **Phase 3** (versioned
pipeline, the 9-stage New business pipeline with proof-checked exit criteria, deal page, catalog
and discount approvals, activities, Today, Kanban board, stage pages, account history, Lost/Won,
profiles and monitoring, exports, delete and restore). Pilot and go-live: `docs/runbooks/pilot-golive.md`.
Phase 4+ (proposals, billing...) is not started. Role guides: `docs/guides/`.

## Run it without Docker (quickest)

Needs Python 3.12 and Node 22. An embedded PostgreSQL 16 is started for you.

```
cd api
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"   # Windows (use .venv/bin on Linux/macOS)
.venv/Scripts/python scripts/run_local.py                                   # API on http://localhost:8000/docs
```
In a second terminal:
```
cd web && npm install && npm run dev      # http://localhost:5173  -> "Local development login"
```
`run_local.py` applies migrations, installs the job-queue tables, seeds 2 teams and 8 people
(`samar@relay.local` Super admin, `anita@` Admin, `tara@`/`tejas@` Team Leaders, 4 reps) and runs
the API and the worker. Data is kept in `api/.localdata`; uploaded files go to
`api/.localdata/pg-files` (no MinIO needed). Emails are not sent in this mode; use Docker for that.

## Run it with Docker

```
cp .env.example .env
make dev        # postgres, minio, mailpit, api (:8000), worker, web (:5173)
```
Mailpit http://localhost:8025, MinIO console http://localhost:9001 (minio / minio12345).
No `make` (Windows)? Run the commands from the `Makefile` by hand.

## Tests and checks

```
cd api && python -m pytest --cov=app.platform --cov-fail-under=90   # real Postgres, never SQLite
cd api && ruff check . && ruff format --check . && mypy app
cd web && npm run lint && npm run typecheck && npm test && npm run build
```
Backend tests start an embedded PostgreSQL automatically; set `TEST_ADMIN_URL` to use another server.

Load a real organisation from a file (teams, people, assignment rules): see
`api/scripts/org_config.example.json` and `python scripts/load_org.py <file>`.
Load check (2,000 open deals, 50 users): `python scripts/run_local.py --reset --data load --port 8003 --load 2000`,
then `python scripts/loadtest.py run --url http://localhost:8003 --users 50`.

## Layout

`api/app/core` shared basics, `api/app/platform` the Phase 1 base (permissions, reasons, audit,
outbox, jobs, notifications, approvals, realtime, files), `api/app/modules` one folder per CRM area,
`web/src/features` mirrors the modules. Every write goes through `platform/uow.py`:
permission, scope, reason, then change + audit row + outbox event in one transaction.
The web types in `web/src/api/types.ts` are generated from the API (`make types`), never hand-written.

Decisions: `docs/adr/`. Operations: `docs/runbooks/` (restore drill, deploy, secrets).

## Notes

- Everything is configured by environment variables (`.env.example`). Moving to AWS (Cognito, S3,
  SES, RDS) is a config change.
- Times are stored in UTC and shown in IST. Money is integer paise.
- Windows: if the project path contains `&`, `npm run <script>` fails in cmd.exe. Clone to a path
  without special characters, or call tools directly (`node node_modules/eslint/bin/eslint.js src`).
- `/demo` in the web app and `api/demo/` are a throwaway in-memory demo of Phase 2 ideas.
