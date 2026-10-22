# Relay CRM — Codebase Map (all phases)

Purpose: a navigation index, not a spec. Read this first so you open only the 1-2 files that
matter for your question, instead of scanning the repo (64,000+ files, mostly `.venv`/
`node_modules`). For deep detail on Phase 4 (billing/subscriptions/proposals/invoices/payments/
usage/minute-packs), read **`PHASE4_FILE_MAP.md`** at the repo root — it lists every function
file-by-file for that phase. This file covers everything, one line per module.

Structure: `api/` = FastAPI backend, `web/` = React frontend, `usage-api/` = a tiny standalone
service the voice platform side exposes. Every module folder on both sides has its own
`README.md` — when in doubt, `cat` that first; it's always one short paragraph.

Money = integer paise everywhere. Dates/times shown in IST. Every state-changing backend call
goes through `UnitOfWork` (permission + reason + audit row + outbox event, one transaction).

## Backend — `api/app/core/` (shared basics, no business rules)
`config.py` env settings · `db.py` SQLAlchemy engine/session · `security.py` auth providers
(dev JWT / Cognito) · `errors.py` the app's exception types → HTTP status mapping · `money.py`
paise/rupee helpers, Indian digit grouping · `time.py` UTC storage + IST display · `base.py`
SQLAlchemy declarative base + column helpers (`uuid_pk`, `org_fk`, `created_at_col`) ·
`fields.py` / `normalize.py` / `limits.py` / `trigram.py` — smaller shared helpers.

## Backend — `api/app/platform/` (cross-cutting infrastructure, Phase 1)
| Folder | What it is |
|---|---|
| `permissions/` | The role×key×scope matrix (`matrix.py`), `guard.py` evaluates it, `scope.py` filters any query by Own/Team/All. Every `uow.authorize(key, ...)` call goes through here. |
| `approvals/` | A blocked action becomes a pending request instead of an error. `service.py`: `create_request`, `decide`, `register_type` (each module registers its own executor — e.g. billing's invoice-edit approval). |
| `audit/` | Append-only history with a hash chain (tamper-evident). Every `UnitOfWork` write ends with one audit row here. |
| `outbox/` | Events are saved in the same transaction as the change, dispatched after commit (`dispatch_batch`). `uow.emit(...)` writes here. |
| `notifications/` | In-app rows + email (`email.py`: SMTP/SES/memory backend, chosen by config). `service.notify(...)` is the one call every alert uses. |
| `files/` | S3-signed upload/download, permission-checked (`register_file_owner`). |
| `jobs/` | Procrastinate (Postgres-backed job queue). `app.py` lists every periodic task and its cron (outbox dispatch, approval reminders, audit chain check, usage sync, billing alerts, etc). |
| `realtime/` | WebSocket + Postgres LISTEN/NOTIFY, scope-filtered live updates. |
| `reasons/` | The "reason required for this role+action" policy table. |
| `api.py` | Platform-level endpoints: auth, approvals inbox, admin misc. |
| `registry.py` | **Wiring, not logic** — one `setup()` that registers every module's scoped entities, approval executors and outbox handlers. If a new module's writes/reads aren't working, check it's registered here. |

## Backend — `api/app/modules/` (one folder per business area)
| Module | Phase | One-liner |
|---|---|---|
| `users/` | 1 | Users, teams, roles, team moves, password reset. |
| `accounts/` | 2 | Accounts, contacts, duplicate rule, merge. |
| `leads/` (+`leads/assignment/`) | 2 | New lead form, Lead Pool, assignment rules/round-robin/caps. |
| `pipeline/` (+`config/`, `engine/`) | 3 | The stage engine: versions/publish, stage moves, exit criteria, proof, override. **`pipeline/seeds/new_business.py`'s `CRITERIA` dict is the ground truth for every stage's exit criteria** — read it before trusting any frontend assumption about what a stage needs. |
| `deals/` | 3 | Deal page/API, value/date/pricing change rules, Won/Lost/reopen/delete. |
| `catalog/` | 3 | Products, floor prices, discount approvals, deal lines. Phase 4 extended this with pricing-model/pack/fee fields. |
| `documents/` | 3 | Manually-uploaded proposal/agreement/invoice records (distinct from Phase 4's *automated* proposals in `proposals/`). |
| `activities/` | 3 | Interaction log (calls/meetings/etc.), next steps, Today view, Nudge/Stale. |
| `profiles/` | 3 | Rep profile pages, win rate, team monitoring. |
| `exports/` | 3 | Scoped CSV export with reason + log. |
| `customers/` `billing/` `proposals/` `usage_feed/` | **4** | See `PHASE4_FILE_MAP.md`. |
| `churn/` `renewals/` | 5 | Not built yet per PRD phase order (churn records/history, renew/change/cancel). |
| `performance/` `reports/` | 6 | Not built yet (leaderboard, incentives, forecast). |
| `capture/` `ml/` | 7 | Not built yet (web-form/CSV/email capture, win-probability model). |
| `integrations/google/` | side track | Google Calendar/Gmail — not built yet. |

## Frontend — `web/src/`
| Folder | What it is |
|---|---|
| `api/` | Generated types from the backend OpenAPI schema + the `api()`/`get()` HTTP client + WebSocket client. Regenerate with `make types` after a backend schema change. |
| `auth/` | Login (dev email login locally, Cognito in prod), `useAuth()`/`can()` permission checks. |
| `components/` | Shared UI: `ReasonDialog`, `HistoryPanel`, `ApprovalBadge`, `ui.tsx` (`btn`/`field`/`Modal`/`Notice` primitives), `Toast`. |
| `features/<area>/` | One folder per screen area, **mirrors `api/app/modules/<area>/`** — e.g. `features/deals/` talks to `modules/deals/` + `modules/pipeline/engine/`. |

`features/deals/` is the one most recently touched (fit-checklist/pain-point made inline,
stage-gated field visibility, clickable checklist navigation) — see `PHASE4_FILE_MAP.md`'s
frontend section for the exact file-by-file breakdown.

## Finding your way to a specific question
1. Is it about billing/subscriptions/invoices/payments/packs/usage/proposals/customers? →
   `PHASE4_FILE_MAP.md`, done.
2. Otherwise, find the module in the table above, `cat api/app/modules/<module>/README.md`
   (one paragraph), then open only `models.py` (data shape), `service.py` (business logic) or
   `router.py` (endpoints) as needed — not all three unless the question spans them.
3. If it's about *why* a stage needs a certain field/activity/document, the answer is always in
   `api/app/modules/pipeline/seeds/new_business.py`'s `CRITERIA` dict — never guess it from the
   frontend.
4. If it's cross-cutting (permissions, reasons, audit, notifications, approvals, jobs) → the
   matching `api/app/platform/<name>/` folder.
