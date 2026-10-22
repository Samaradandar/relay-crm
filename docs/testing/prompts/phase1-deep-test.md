# Phase 1 deep test prompt: "The base"

First read `docs/testing/COMMON.md` and follow it exactly. Spec: `Relay_CRM_Phase1_Developer_Prompt.md` and PRD v3.6
sections 4 (roles, scope, reasons, approvals, audit), 12-14 and the Phase 1 tab of the Build Phases plan.

**Your folders** (create them): `api/tests/deep/phase1/` (add an empty `__init__.py` in `api/tests/deep/` and in yours),
`web/e2e/phase1/`. Report: `docs/testing/reports/phase1-report.md`. Browser ports: API 8101, web 5201,
data folder name `qa1`, `E2E_REPORT=e2e-report-phase1.json`, `E2E_SCREENS=e2e-results-phase1`.

Phase 1 is the foundation, so a mistake here breaks every phase and every customer. Test as if you were an attacker
and an auditor.

## 1. Identity and login
- Dev login: unknown email, inactive user, deleted user, mixed-case and padded email, empty body, wrong types.
- Tokens: missing, malformed, expired, signed with another key, role claim tampered, token of a user who has since
  been deactivated (must stop working on the **next** request), token of a user whose role changed (new role applies
  at once).
- Every route except health and login must answer 401 without a token (enumerate the routes from the OpenAPI
  schema of the app and test them all programmatically).
- `/me`: permissions list equals the matrix for the caller's role, for all four roles.

## 2. Permission matrix (exhaustive)
- Parse `matrix.py` independently (or restate the PRD table by hand for at least the first 30 keys) and assert,
  through `guard.evaluate`, that every (role, key) pair gives the right effect, scope and flags
  (`own_deals_only`, `not_own_deals`, approval routing). Assert unknown keys raise.
- Every key has a class (important / routine / view) and a row for each role; the sync command is idempotent;
  the database copy equals the Python copy; a deleted or altered row changes behaviour only after the cache is
  invalidated (document what happens).
- Through HTTP, for every Phase 1 endpoint x every role x (own, same-team, other-team, other-org) assert the exact
  status code (200/201/202/204/403/404/409/422) and that a refused write leaves no change and writes a `denied` audit row.

## 3. Scope and cross-organisation isolation (highest priority)
- Create two organisations with users in each. For **every** endpoint that takes an id or returns lists
  (users, teams, approvals, notifications, history, files, events, failed jobs, websocket), prove org B can never
  read, change, count or even detect org A's records (404 not 403, same response shape for "missing" and "other org").
- Own / Team / All filtering on the user list, approvals inbox, history, notifications, events catch-up and the
  websocket: for each role assert exactly which rows are returned. Super admin-owned records are hidden from
  Team Leaders and Users.
- Search and pagination: `limit`/`offset` extremes, negative values, huge values; ordering stability.

## 4. Reason policy
- Table role x class (important, routine, view): required / optional / none. Whitespace-only, 1 character, 10,000
  characters, unicode, emoji, HTML, SQL text: stored verbatim and shown escaped, never executed.
- The person whose record changed can see the reason (history endpoint) and nobody out of scope can.

## 5. Audit trail
- Every successful write path in Phase 1 produces exactly one `ok` audit row with who, when, old, new, reason, origin;
  refused attempts produce `denied` rows; approval-executed changes record `on_behalf_of` and the approval id.
- Hash chain: verify passes after normal use; **tamper** with a row through the admin connection (change a value,
  swap two rows, delete a row, insert a row) and prove `verify_chain` reports the first bad sequence number each time.
- The application database role cannot UPDATE, DELETE or TRUNCATE `audit_log` (try each and expect the permission
  error); the database trigger also blocks the superuser's UPDATE/DELETE (try).
- Concurrency: 50 simultaneous writes in one org keep `seq` gapless and the chain valid.
- The nightly check job notifies Super admins when the chain is broken (and only them), once per break.

## 6. Outbox and jobs
- At-least-once: crash a handler halfway, run dispatch again, assert effects happen once (dedupe keys).
- Retry/backoff schedule, the 10-attempt limit, status `failed`, the Failed jobs list (permission `jobs.view_failed`),
  retry from there (permission `jobs.retry`), a poison event does not block others, two dispatchers at once never
  process the same event twice (`FOR UPDATE SKIP LOCKED`).
- Procrastinate schema applies twice without error; the periodic tasks are registered with the documented schedules.

## 7. Notifications
- Required types cannot be switched off, optional ones can; settings are per user; mark-read only affects the
  caller's own rows (try another user's id); inactive users receive nothing; dedupe key prevents duplicates; the
  email event is queued once.

## 8. Approvals
- Routing: team-level requests go to the requester's Team Leader, blocked actions to the Admin/Super admin pool.
- Self-approval matrix: User and TL cannot approve their own; Admin cannot approve their own or a request about a
  record they own; Super admin may approve their own.
- Race: 20 simultaneous approve/reject calls on one request: exactly one executes, others get 409, executor ran once.
- Reject, cancel (only by requester), already-decided, unknown id, other org's id. A reason is mandatory to request;
  decisions follow the reason policy. Requests never expire; the daily reminder is idempotent per day.
- Executors: `user.create` and `user.change_role` apply the right change exactly once, with audit rows.

## 9. Live updates
- Websocket: bad token, expired, deactivated user is disconnected, event fan-out only to users whose **current**
  role/team allows it (change a user's team mid-connection and prove it), `/events?since=` catch-up returns only
  in-scope events in order with no gaps or duplicates, reconnect resumes.

## 10. Files
- Upload URL: needs the permission and the owner record in scope; size 0, max, max+1; weird filenames (`../`, long,
  unicode, null byte); complete before upload (409); complete by someone else; download by out-of-scope user (404);
  other org's file id; deleted file. Use the fake storage pattern from `api/tests/foundation/test_platform_services.py`.

## 11. Users and teams
- Create/rename/deactivate/reactivate/role change/team move with every role and target role. Only Super admin
  creates or removes Admins and Super admins; a User must be in a team; the last Super admin cannot be demoted or
  deactivated (also under 10 concurrent attempts); email uniqueness is case-insensitive among live users; a
  deactivated user can be re-created? (check the PRD); password reset rules (own for User, others with reason).
- Team Leader assignment: one team per TL, many TLs per team (PRD). Moving a user fires the hook and notifies them.

## 12. Operations
- Migrations: empty database to head works; running grants twice is harmless; the `relay_app` role has exactly the
  intended privileges (no DDL, no audit UPDATE/DELETE). `api/scripts/backup_restore.py`: backup then restore into a
  fresh database and compare row counts and the audit chain.
- Config: app refuses to start with a missing/placeholder secret in production mode (check `core/config.py`;
  report if it does not). Error bodies never contain stack traces or SQL. CORS allows only the configured origins.
  Validation error format is `{code, message, details}` everywhere.
- Abuse: 1,000 rapid login attempts and 10 MB bodies: report what happens (rate limiting is not built; list as a
  GAP/RISK with numbers).

## 13. Browser (Playwright, folder `web/e2e/phase1/`)
Start the stack described in COMMON.md. Cover: the login page (dev login for each role, wrong email message);
navigation shows exactly the items each role is allowed; Users page (create a user with the reason dialog as Admin,
as Team Leader it becomes an approval request, change role, deactivate) and Teams page; Approvals inbox end to end
(rep/TL requests, another person approves, status updates live without reload); notification bell (unread count,
mark read); Failed jobs page; session expiry (remove the token, reload, expect the login page); a 403 shows a
friendly message; phone width 400 px has no horizontal scroll on every Phase 1 page; keyboard-only completion of the
reason dialog. Capture console errors on every page.
