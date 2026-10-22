# Phase 1 deep test report: "The base"

## Summary

| | |
|---|---|
| Date | 2026-10-07 |
| Tester | QA agent for Phase 1 (instructions: `docs/testing/COMMON.md`, `docs/testing/prompts/phase1-deep-test.md`) |
| Scope | Sections 1-13 of the Phase 1 prompt: identity and tokens, permission matrix, isolation between organisations, reason policy, audit trail, outbox and jobs, notifications, approvals, live updates, files, users and teams, operations, browser. Product code was not changed. |
| Backend tests written | **460** in `api/tests/deep/phase1/` (428 pass, **32 strict xfail = known bugs/gaps**, 0 fail). Includes 3 seeded model-based fuzz runs (350 steps each), a hand-restated matrix of all 80 keys x 4 roles, and 4 timing/volume tests. |
| Browser specs | **66** in `web/e2e/phase1/` (61 pass, **5 are `test.fail()`** for known UI bugs, 0 unexpected failures). Every spec fails on any console error, page error or unexpected HTTP error >= 400. |
| Whole backend suite | Final run without the other testers' unfinished folders (`tests/deep/phase2`, `phase3`, `phase4`): **630 passed, 1 skipped, 32 xfailed, 0 failed** (my 32 xfails). A run that included `tests/deep/phase3` showed 38 failures, all inside that other tester's folder while it was still being written; nothing outside it failed. |
| Servers | API 8101 (embedded Postgres data folder `qa1b`, see note), web 5201. Both stopped by task id. |

Decisions and deviations (nobody could be asked):

* The data folder `qa1` was locked by a database process left from my first start (the API had to be restarted because the default CORS list allows only `http://localhost:5173`; this is correct behaviour). I used `qa1b` for the rest of the run.
* The prompt says "many TLs per team (PRD)". PRD 4.3 says "A team has exactly one Team Leader"; 4.1 says "there can be many TLs" (many teams). I followed the PRD text: one TL per team.
* The PRD matrix gives a Team Leader "No" for resetting a password; the Phase 1 prompt section 10 says everyone may reset their own. I followed the prompt (matrix key `password.reset_own`) and list it as a NOTE.
* The PRD matrix says a TL may not edit an invoice, PRD 4.5 says invoice-edit requests go to the Admin pool; the code follows 4.5. Not a Phase 1 flow; listed as an open question.
* `/me` is checked for every role against my own hand-written table, not against `matrix.py`.
* "Gapless seq" was interpreted as: no gaps inside one org's chain when only that org writes (identity values are global and shared by all orgs, so gaps are normal across orgs).
* Tests for later-phase routes are included only where they are part of the "every route needs a token" sweep (all 90+ routes of the app are covered by that sweep).

Not tested, and why:

* Real S3/MinIO and SMTP/SES (not available in this mode); a fake storage and the in-memory e-mail backend were used, as the existing suite does. The size cap on the signed PUT URL could therefore only be checked as a contract (see N-3).
* Cognito (needs AWS); only its refusal rules were exercised.
* Real rate-limiting and WAF behaviour (not built).
* Multi-process LISTEN/NOTIFY fan-out (one API process only).
* Browser: Google sign-in button (needs Cognito), real e-mail delivery.

## Coverage map

| Area (prompt section) | What was tested | Files |
|---|---|---|
| 1 Identity and login | unknown / inactive / deleted / mixed-case / padded e-mail, 8 bad bodies, wrong content types, forged / expired / wrong-key / `alg none` / HS512 / tampered-role tokens, deactivated user stops on next request and works again after reactivation, role change applies at once, teamless user refused, every route answers 401 without token and with garbage/expired token (enumerated from OpenAPI), `/me` equals the matrix for 4 roles | `test_01_identity.py` |
| 2 Permission matrix | hand table of all 80 keys, class and 4 role cells, through `guard.evaluate` on the DB copy and the Python copy including the `own_deals_only` / `not_own_deals` fail-closed cases; unknown keys raise; DB completeness and CHECK constraints; sync idempotent and repairing; cache behaviour after a DB edit; deleted row; `/me`; HTTP matrix role x action (rename, deactivate, role, move, reset) x 8 target kinds (self, same team, other team, own TL, other TL, Admin, Super admin, other org) with exact status, denied audit row and no change; create-user matrix 4 roles x 4 target roles; teams; failed jobs | `test_02_matrix.py` |
| 3 Scope and isolation | org B vs every id route of org A (users, teams, approvals, history, notifications, files, events, failed jobs), "missing" and "other org" answers equal, nothing changes, no org id leaks in any list; exact row sets per role for users, user detail, approvals inbox (3 tabs), approval detail, history, events; Super-admin-owned hiding; pagination extremes; 105 pending requests | `test_03_isolation.py` |
| 4 Reason policy | role x class table, blank / whitespace / NBSP / ideographic space, zero-width, 12 hostile payloads (10,000 chars, HTML, SQL, emoji, RTL, template syntax) stored verbatim and returned as JSON data, e-mail escaping, decision reasons per role over HTTP | `test_04_reasons.py` |
| 5 Audit | exactly one ok row (with who, when, old, new, reason, origin, request id, user agent, on-behalf-of, approval id) for every Phase 1 write path; denied rows; atomicity when the outbox insert fails; chain verification; tamper by value (10 fields), swap, delete first / middle, insert at end / middle, per-org verification; tail truncation, wipe and re-computation (known weakness); app role cannot UPDATE/DELETE/TRUNCATE, cannot bypass guards; trigger stops the superuser; 50 simultaneous writes keep seq gapless and the chain valid; mixed concurrent writes; nightly check notifies only active Super admins of the broken org, once | `test_05_audit.py` |
| 6 Outbox and jobs | crash after effect and before done, crash halfway, dedupe on replay, backoff 2,4,8... and 10-attempt limit, due times, poison event, 6 dispatchers x 60 events, hanging handler, failed jobs list / retry over HTTP, schedules, IST arithmetic, reminder idempotence, app-role privileges after setup | `test_06_outbox_jobs.py` |
| 7 Notifications | the 6 required types ignore switches, optional channels per user, inactive / foreign / unknown users, dedupe, one e-mail per notification, mark-read only for the caller, bad bodies, real flows (invite, team move, role change) | `test_07_notifications.py` |
| 8 Approvals | routing, self-approval matrix (11 cases), 20 simultaneous decisions (two routes), cancel-vs-approve race, reject / cancel / already decided, unknown and malformed ids, mandatory request reason, never expire, reminders, `user.create` and `user.change_role` executors exactly once, stale requests | `test_08_approvals.py` |
| 9 Live updates | real uvicorn + real websockets: refused tokens, fan-out by scope and org, team move and role change mid-connection, deactivation closes socket, Super-admin-owned hidden, reconnect + catch-up without gaps or duplicates, 200 cap, starvation, token expiry, 60-socket cost | `test_09_live.py` |
| 10 Files | permission and scope, size boundaries and wrong types, 18 hostile file names, content types, complete (before upload, twice, by someone else, other org, real size, oversize), download matrix and redirect, pending / deleted / orphan files, owner moves team | `test_10_files.py` |
| 11 Users and teams | create rules, name / e-mail boundaries, case-insensitive uniqueness and a 12-way race, role-change paths and leader bookkeeping, all-or-nothing PATCH, last Super admin (3 SAs, 10 concurrent attempts, double self-demotion), move hook fired once and rolled back, team names, DB uniqueness of leader and open membership, password reset rules and provider calls | `test_11_users_teams.py` |
| 12 Operations | empty DB to head twice, grants twice, backup then restore (all tables, chain, triggers, privileges, sequences), corrupt restore, production refusal, CORS, error envelope on 9 error kinds and on every POST/PATCH route, public docs, 1,000 logins, 1 MB and 10 MB bodies | `test_12_operations.py` |
| Gaps / bugs not tied to one section | request-only role, team history, deactivation request | `test_90_known_gaps_and_bugs.py` |
| Performance | 1,500 users / 60 teams / 3,000 pending / 50k outbox / 3,000 history rows; audit append with 400k other-org rows; verify of 30,000 rows | `test_13_performance.py` |
| Model-based fuzz | 3 seeds x 350 random legal / illegal steps by random roles, then 20 invariants and cross-checks (chain, one open membership per user, one leader per team and active TLs lead a team, one live e-mail, approved `user.create` created exactly one user, no duplicate notification keys, denied rows == refused writes, org B untouched, member counts and directory sizes recomputed in SQL) | `test_95_fuzz_and_invariants.py` |
| 13 Browser | login page (4 roles, unknown / deactivated / teamless messages, keyboard), session expiry and sign-out, nav per role, forbidden URLs, Users page (reason dialog for Admin, Super admin, TL request, duplicate e-mail, role / team / deactivate, 403 message, HTML in names and reasons), Teams page, Failed jobs page (escaping, retry), Approvals end to end with live update on two browsers, reject / cancel / stale request, bell, 400 px width on 12 pages and 3 dialogs, keyboard-only reason dialog, labels, console cleanliness on every spec | `web/e2e/phase1/login.spec.ts`, `admin.spec.ts`, `approvals.spec.ts`, `responsive-keyboard.spec.ts`, helpers `kit.ts`, `seed_failed_job.py` |

## Findings

Fix status (QA fix pass): every finding is marked **Status** below. Not fixed: BUG-P1-19 (note), N-1 rate limiting (product decision; a limiter would also break the login-flood test), N-1 to N-18 notes. The strict xfail / `test.fail` marks were removed; the tests now pass normally.

Severity: S1 data loss / cross-org or cross-role leak / security hole, S2 wrong behaviour in a main flow, S3 wrong in an edge case or poor message, S4 cosmetic.
All "Test" entries are strict xfail (backend) or `test.fail()` (browser) unless noted. Numbers N-x are notes without a test mark.

### BUG-P1-01 - BUG - S3 - A NUL byte in any text field answers HTTP 500
* **Status: Fixed. Database data errors (NUL byte, number out of range) answer 422 through a handler in `app/core/errors.py`.**
* Steps: `POST /auth/dev/login {"email": "\u0000@x.com"}`; `POST /users` with `"name": "a\u0000"` or `"reason": "r\u0000"`; `POST /teams {"name": "t\u0000"}`; `PATCH /users/{id}` name.
* Expected: 4xx `{code, message, details}` (PRD 12 error format; 4.2 "any non-empty text is accepted" does not cover a character PostgreSQL cannot store).
* Actual: unhandled `asyncpg CharacterNotInRepertoire` -> 500 plain text.
* Test: `test_01_identity.py::test_nul_byte_in_login_email_is_a_clean_4xx`, `test_11_users_teams.py::test_nul_in_names_reason_and_team_names_does_not_500`.
* Fix: strip or reject `\u0000` in a shared Pydantic string type, and add a generic exception handler (see BUG-P1-21).

### BUG-P1-02 - BUG - S4 - `/me` permission field is `action_class`, the published schema says `class`
* **Status: Fixed. `/me` now serialises `class` as the schema and the generated frontend types say.**
* Steps: `GET /me`, compare with `/openapi.json` (`PermissionOut.class`, required) and the generated `web/src/api/types.ts`.
* Expected: response matches the schema the frontend types are generated from.
* Actual: `response_model_by_alias=False` serialises `action_class`. The frontend does not read it yet, so nothing is broken today.
* Test: `test_01_identity.py::test_me_permission_field_matches_the_published_schema`.
* Fix: serialise by alias (remove `response_model_by_alias=False`) or rename the field.

### BUG-P1-03 - BUG - S3 - A request-only role performs the action directly (rename, file attach)
* **Status: Fixed. Rename/team move/file upload by a request-only role answer 403 (denied row); no rename request type exists.**
* Steps: as Team Leader `PATCH /users/{team member}` `{"name": "...", "reason": "r"}` -> 200; same for the TL's own record; `POST /files/upload-url` for a team member's user record -> 200.
* Expected: `user.manage` is "Request only" for a TL (PRD 4.4 L, matrix mode `request_only`: "the actor never performs it"). Role change and create honour this; rename and upload ignore the decision's effect.
* Actual: the change is made immediately with an audit row; also a combined `{"role": ..., "name": ...}` returns 200 and hides the approval request that was created for the role part.
* Test: `test_90_known_gaps_and_bugs.py::test_request_only_role_cannot_rename_directly`, `..._themselves_directly`, `test_10_files.py::test_team_leader_cannot_attach_files_to_user_records`.
* Fix: in `UnitOfWork.authorize` callers (or inside `authorize`) refuse `REQUEST_ONLY` / `NEEDS_APPROVAL` unless the caller is the request path.

### BUG-P1-04 - BUG - S3 - Whitespace-only names are accepted and stored empty
* **Status: Fixed. Names are trimmed and must be non-empty (`PersonName`, `TeamName`).**
* Steps: `POST /users` `{"name": "   ", ...}`; `PATCH /users/{id}` `{"name": "  "}`; `POST /teams {"name": "   "}`.
* Expected: 422 (a name is required; compare reasons, where whitespace counts as empty).
* Actual: 201 / 200 with `name: ""`; the Users and Teams tables then show a blank row.
* Test: `test_11_users_teams.py::test_whitespace_only_names_are_rejected` (4 variants).
* Fix: strip before the length check (`StringConstraints(strip_whitespace=True, min_length=1)`).

### BUG-P1-05 - BUG - S3 - Demoting an Admin or Super admin to User leaves a team-less, locked-out account
* **Status: Fixed. Making an Admin/Super admin a User or Team Leader needs `team_id` in the same PATCH (409 otherwise); the person joins that team.**
* Steps: Super admin `PATCH /users/{admin id} {"role": "user"}` -> 200; the person's next request is `403 You are not in a team yet`.
* Expected: PRD 4.3 "A User belongs to exactly one team"; the change should demand a team (409/422) or place the person.
* Actual: 200, `team_id` null, account unusable until someone notices.
* Test: `test_11_users_teams.py::test_demoting_a_privileged_user_to_user_does_not_leave_a_locked_out_account`.
* Fix: require `team_id` in the same PATCH when the new role is `user`/`team_leader` and the person has none.

### BUG-P1-06 - BUG - S3 - Bell "unread" count stops at 50
* **Status: Fixed. `unread` is a real count (`notifications.unread_count`).**
* Steps: a user with 60 unread notifications; `GET /notifications` -> `unread: 50`.
* Expected: PRD "bell with an unread count": 60.
* Actual: `unread` is computed over the 50 rows returned; the list also has no paging, so older ones cannot be reached.
* Test: `test_07_notifications.py::test_unread_count_is_not_capped_at_the_page_size`.
* Fix: `SELECT count(*) WHERE read_at IS NULL`; add `limit/before` paging.

### BUG-P1-07 - BUG - S4 - Integers beyond 64 bits answer 500
* **Status: Fixed. `since`, `before_seq`, path ids are bounded to 64-bit (422).**
* Steps: `GET /events?since=99999999999999999999`, `GET /history/user/{id}?before_seq=<25 digits>`, `POST /admin/failed-jobs/<25 digits>/retry`.
* Expected: 422.
* Actual: asyncpg `DataError` -> 500.
* Test: `test_03_isolation.py::test_huge_numbers_in_query_strings_are_4xx_not_500`.
* Fix: `Query(ge=0, le=2**63-1)` / `Path(le=...)`.

### BUG-P1-08 - BUG - S3 - Retrying an unknown or not-failed job answers 204 and writes an "ok" audit row
* **Status: Fixed. Unknown or not-failed job: 404, no audit row.**
* Steps: `POST /admin/failed-jobs/987654321/retry` (or the id of a pending/done event) as Admin with a reason.
* Expected: 404 (unknown) / 409 (not failed); no "ok" row for a change that did not happen.
* Actual: 204, audit row `jobs.retry ok`; `retry_failed()` returns False and the result is ignored.
* Test: `test_06_outbox_jobs.py::test_retry_of_an_unknown_or_not_failed_job_is_refused`.
* Fix: check the return value, raise `NotFound`/`Conflict` before auditing.

### BUG-P1-09 - BUG - S3 - The approvals inbox shows only the newest 100 and cannot be paged
* **Status: Fixed. `/approvals` takes `limit` (default and max 500) and `offset`. No UI "load more" yet.**
* Steps: 105 pending requests; `GET /approvals?tab=pending` returns 100.
* Expected: PRD 4.5 "Pending requests never expire; the approver gets a daily reminder until they answer" - the oldest must stay reachable.
* Actual: `limit=100` hard-coded, no offset/cursor; the reminder job still nags about requests nobody can open from the list. Same for `decided` and `mine`.
* Test: `test_03_isolation.py::test_pending_inbox_is_capped_at_100_and_oldest_requests_become_unreachable`.
* Fix: `limit`/`before` query parameters, UI "load more".

### BUG-P1-10 - RISK - S2 - The audit chain cannot detect deletion of the newest rows, a wiped trail, or a recomputed chain
* **Status: Fixed (in-database anchoring). Migration 0004 adds insert-only `audit_anchor` (org, seq, row_hash), written with every audit row; `verify_chain` flags a missing or different anchor. Remaining limit: a database superuser can also edit the anchor table, so an external copy (object lock) is still recommended; this is open question 8.**
* Steps (superuser with the trigger disabled): delete the last N rows of an org; or delete all rows; or edit a row and recompute `prev_hash/row_hash` of every later row. `verify_chain` returns `ok=True` each time (an empty trail is "ok, checked 0").
* Expected: ADR 0003 / PRD 12 "tamper-evident"; the nightly job should alert on any of these.
* Actual: only edits, middle deletions, swaps and inserts are detected (all proved). Nothing anchors the head hash or the row count outside the table.
* Test: `test_05_audit.py::test_tamper_truncating_the_tail_is_detected`, `..._wiping_the_whole_trail_...`, `..._edit_and_recompute_...` (3 xfail).
* Fix: store a daily signed head (hash, count, max seq) outside the database role's reach (S3 object lock / separate table owned by another role) and compare in the nightly check; alert when the stored head is not reproducible.

### BUG-P1-11 - BUG - S2 - After the documented setup step the app role can TRUNCATE business tables and CREATE TRIGGER on `audit_log`
* **Status: Fixed. `jobs/schema.py` no longer uses `GRANT ALL`; it grants only the job-queue tables and revokes TRUNCATE/TRIGGER/REFERENCES.**
* Steps: run `python -m app.platform.jobs.schema` (required after `alembic upgrade`, also run by `run_local.py`), then connect as `relay_app`: `TRUNCATE users` works; `CREATE TRIGGER x BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()` works (it would reject every later audit insert).
* Expected: Phase 1 prompt 7 / COMMON "no DDL, no audit UPDATE/DELETE"; "nothing is hard-deleted" (migration grants only SELECT/INSERT/UPDATE/DELETE).
* Actual: `GRANT ALL ON ALL TABLES` (and `ALL FUNCTIONS`) in `jobs/schema.py` adds TRUNCATE, TRIGGER, REFERENCES on every table, then only `UPDATE, DELETE, TRUNCATE ON audit_log` are revoked again (TRIGGER and REFERENCES remain).
* Test: `test_06_outbox_jobs.py::test_app_role_has_only_select_insert_update_delete_after_setup`, `..._cannot_create_a_trigger_on_the_audit_log`, `..._cannot_truncate_business_tables`. What passes: UPDATE/DELETE/TRUNCATE on `audit_log` are refused with "permission denied" before and after the setup step; the superuser is stopped by the trigger.
* Fix: grant `SELECT, INSERT, UPDATE, DELETE` (and `USAGE` on sequences) on the procrastinate tables only; never `ALL`.

### BUG-P1-12 - BUG - S3 - With the in-app channel off, the e-mail is queued again on every outbox retry
* **Status: Fixed. E-mail is deduped on its own key when the in-app channel is off (index in migration 0004).**
* Steps: optional type, user setting `in_app=false, email=true`; `notify(... dedupe_key="k")` three times (an outbox retry).
* Expected: PRD "dedupe" / ADR 0002: one e-mail.
* Actual: three `email.send` events. `fresh` stays True when the in-app insert is skipped, so the dedupe key is never consulted.
* Test: `test_07_notifications.py::test_email_is_queued_once_when_only_the_email_channel_is_on`.
* Fix: dedupe e-mail by `(user, dedupe_key)` independently (insert the in-app row as "hidden" or keep a separate dedupe table).

### BUG-P1-13 - RISK - S3 - An open WebSocket keeps receiving events after its token expired
* **Status: Fixed. The socket ends with code 4401 at the token `exp`.**
* Steps: connect with a token valid for 3 s, wait, emit an event the user may see: it is delivered.
* Expected: "authenticated with the same token" - an expired token should end the session.
* Actual: the token is checked once at connect; later events only re-read the user row. (A deactivated user is disconnected: proved.)
* Test: `test_09_live.py::test_socket_stops_receiving_after_the_token_expires`.
* Fix: store `exp` on the connection and close with 4401 when it passes.

### BUG-P1-14 - BUG - S3 - `/events?since=` can starve a client of visible events
* **Status: Fixed. `/events` scans in batches until it finds visible events.**
* Steps: 250 events of another team followed by one visible event; client with `since=<before>`.
* Expected: the visible event is reachable ("no gaps", Phase 1 prompt 8).
* Actual: the 200-row limit is applied before the scope filter, so the first page is empty and the client (which only advances `since` from events it received) never reaches the visible one. After a night offline in an org with more than 200 events this is the normal case.
* Test: `test_09_live.py::test_catch_up_is_not_starved_by_a_long_run_of_invisible_events`.
* Fix: page by scanning, return a `next_since` cursor even when the page is empty, or filter in SQL.

### BUG-P1-15 - BUG - S4 - An out-of-scope file answers differently from a missing file
* **Status: Fixed. Hidden file answers `File not found` like a missing one.**
* Steps: `GET /files/{id}` of a file the caller may not see -> 404 `Not found`; of a missing id -> 404 `File not found`.
* Expected: PRD / prompt 4 "404 so existence does not leak".
* Actual: the two messages differ (other organisations are already identical).
* Test: `test_10_files.py::test_hidden_file_answers_exactly_like_a_missing_file`.
* Fix: use one message for both.

### BUG-P1-16 - BUG - S4 - Audit `entity_id` keeps the client's spelling
* **Status: Fixed. Entity ids are canonicalised to the UUID spelling.**
* Steps: `POST /files/upload-url` with `entity_id` in upper case; the audit row stores the upper-case text, so `/history/user/<lower-case id>` does not show `file.upload`.
* Expected: history of one record is complete.
* Test: `test_10_files.py::test_uppercase_or_wrapped_entity_ids_do_not_split_the_history`.
* Fix: store `str(uuid.UUID(entity_id))` in `create_upload` / `authorize`.

### BUG-P1-17 - GAP - S3 - History of teams (and of failed-job retries) cannot be read
* **Status: Fixed. `team` is a scoped entity (leader of that team and Admins) and `outbox_event` history needs `jobs.view_failed`.**
* Steps: Admin creates and renames a team (two audit rows exist); `GET /history/team/{id}` -> 404 for every role; same for `outbox_event`.
* Expected: PRD 4.8 / 12 "every change is recorded and visible in history"; prompt 7 History API for any record.
* Actual: `authorize_read` answers 404 for entity types not registered with the scope registry (only user, approval, deal, account are).
* Test: `test_90_known_gaps_and_bugs.py::test_team_history_is_viewable_by_an_admin`, `test_failed_job_retry_history_is_viewable`.
* Fix: register `team` (Admin/SA only) and `outbox_event` in `registry.setup()`.

### BUG-P1-18 - GAP - S3 - A Team Leader cannot request a deactivation (and the UI offers no role-change request)
* **Status: Fixed. New approval type `user.deactivate`; Team page offers "Request deactivation" and a role-change request select.**
* Steps: TL `PATCH /users/{team member} {"is_active": false, "reason": "..."}` -> 403 "Team Leaders cannot deactivate users yet". The Team page shows the role as plain text for a TL (`requestOnly`), so only "Request new user" exists.
* Expected: matrix `user.manage` TL = "Request only" for create, **deactivate** and change role (PRD 4.4 L); prompt 9 registers only `user.create` and `user.change_role`, so deactivation is a documented omission.
* Test: `test_90_known_gaps_and_bugs.py::test_team_leader_can_request_a_deactivation`. UI part: no test (observed on `/team`).
* Fix: add a `user.deactivate` request type; add the role-change request button.

### BUG-P1-19 - NOTE - S4 - A PATCH that changes nothing skips the permission check
* **Status: Not fixed (NOTE; a no-op PATCH is harmless, left as documented).**
* Steps: a plain User sends `PATCH /users/{team mate} {"role": "user"}` (the current value) -> 200, no denied row; for someone outside the team -> 404.
* Expected: 403 + denied audit row like any other attempt; at least no 200.
* Test: `test_11_users_teams.py::test_patch_with_nothing_to_change_skips_the_permission_check` (documents current behaviour, passes).

### BUG-P1-20 - RISK - S2 - The application starts in production with the dev provider; it only fails on the first login
* **Status: Fixed. `create_app()` calls `validate_for_start()` and refuses ENV=production with the dev provider or missing Cognito ids. The insecure defaults are unchanged (tests and local use depend on them).**
* Steps: `Settings(env="production", auth_provider="dev")` -> `create_app()` and `/health` work; `get_auth_provider()` raises only when the first token is checked. The defaults (`env=local`, `auth_provider=dev`, public JWT secret `local-dev-secret-change-me-32-bytes-min`, DB `relay_app:relay_app`) are all insecure; if `ENV` is forgotten in production, `POST /auth/dev/login` mints a token for any existing e-mail.
* Expected: `docs/runbooks/secrets.md` "the app refuses ENV=production with the dev provider"; COMMON "app refuses to start with a missing/placeholder secret in production".
* Actual: refuses lazily (proved: `test_dev_provider_refuses_production_when_first_used` passes); no start-up validation of placeholders; the safe default is not the default.
* Test: `test_12_operations.py::test_app_refuses_to_start_in_production_with_the_dev_provider` (xfail); evidence test `test_the_defaults_are_the_insecure_development_ones` (passes).
* Fix: validate settings in `create_app()`/lifespan; default `AUTH_PROVIDER` to cognito unless `ENV` is `local` or `test`.

### BUG-P1-21 - BUG - S4 - Unhandled errors answer plain text, not the `{code, message, details}` envelope
* **Status: Fixed. Unhandled errors answer `{code, message, details}` with status 500.**
* Steps: trigger any 500 (BUG-P1-01). Body is `Internal Server Error`. (It never contains a stack trace or SQL: proved on 9 error kinds.)
* Test: `test_12_operations.py::test_unhandled_errors_also_use_the_envelope`.
* Fix: `@app.exception_handler(Exception)` returning the envelope with a request id.

### BUG-P1-22 - RISK - S3 - No size limit on reasons or request bodies
* **Status: Fixed. `reason` max 20,000 characters on user/team/approval bodies; 2 MB request body limit (413) in `app/core/limits.py`. Reason fields of other modules are not limited yet.**
* Steps: a 10 MB reason is accepted (0.3 s) by `PATCH /users/{id}` and stored in the audit log, approvals and notifications; a 10 MB JSON login body is read in full (0.7 s).
* Expected: sensible maxima (a reason of a few thousand characters) and a body limit at the proxy/app.
* Test: `test_12_operations.py::test_oversized_reason_is_refused`.
* Fix: `max_length` on `reason`, summary and payload strings; body-size middleware.

### BUG-P1-24 - BUG - S4 - Dialogs ignore Escape and do not trap focus (browser)
* **Status: Fixed. `Modal` closes on Escape, traps Tab, restores focus.**
* Steps: open the reason dialog on `/admin/teams`; press Escape (nothing happens); press Tab 8 times (focus leaves the dialog and lands on the page behind it).
* Test: `responsive-keyboard.spec.ts` "BUG-P1-24: Escape closes the dialog", "focus stays inside an open dialog" (`test.fail`). What works: autofocus on the reason box, Tab to Confirm, Enter submits, Confirm disabled for blank / whitespace.
* Fix: handle Escape in `Modal`, trap focus, restore focus on close.

### BUG-P1-25 - BUG - S4 - Inputs without a visible label (browser)
* **Status: Fixed. Visible labels for the new-team field and the reason box.**
* The "New team name" field has only a placeholder; the reason box has only an `aria-label` and no visible label.
* Test: `responsive-keyboard.spec.ts` "BUG-P1-25: ..." (2 x `test.fail`). The add-user dialog fields all have visible labels (test passes).

### BUG-P1-26 - BUG - S3 - The notification panel is cut off at the left edge on a phone (browser)
* **Status: Fixed. The bell panel is fixed with 1 rem margins on phones.**
* Steps: at 400 px open the bell: the 20 rem panel is right-aligned to a button near the left, `x = -13.7 px`.
* Test: `responsive-keyboard.spec.ts` "BUG-P1-26: ..." (`test.fail`). All 12 Phase 1 pages and the dialogs have no horizontal page scroll (pass).
* Fix: `max-w-[calc(100vw-2rem)]` and anchor to the viewport on small screens.

### Notes (no test mark)
* **N-1 Missing feature, no rate limiting.** 1,000 login attempts (500 valid, 500 unknown) took 6.7 s in-process (about 150 per second), all answered 200/401 with no throttling; every valid dev login writes an audit row and updates the user (500 rows). Brute-forcing e-mails is also possible because unknown and inactive accounts are told apart by design (prompt 10). RISK S3 for the Cognito deployment, where Cognito throttles instead.
* **N-2 Public documentation.** `/docs`, `/redoc`, `/openapi.json` and `GET /auth/dev/users` (lists every account of every organisation; dev provider only) are readable without a token.
* **N-3 Upload cap not enforced at the signed URL.** The 25 MB limit is only checked at `complete`; a client can park a larger object in the bucket (the size is rejected afterwards but the object stays). Size 0 / over 25 MB answers 409 (not 422).
* **N-4 Password reset by Admin of a Super admin** is allowed (matrix: Admin = all + reason). The reset mail goes to the Super admin's own address, so it is a nuisance/lock-out vector, not a takeover.
* **N-5 Web socket binary frame** raises an unhandled `KeyError` in the endpoint (logged as ERROR, the server survives, the client is disconnected).
* **N-6 Deactivated person's e-mail stays reserved**; re-adding means reactivating the old account (PRD silent; open question).
* **N-7 A multi-field PATCH writes one audit row per aspect** (rename + team move = 2 rows). Each aspect is exactly one row, but "one change = one row" holds per aspect, not per request.
* **N-8 Approval execution writes two rows** (the executed change and `approval.decide`); both carry on-behalf-of and the approval id.
* **N-9 A TL can queue a request that can never succeed** (a second Team Leader for a team that has one; fails with 409 at approval time and stays pending until rejected).
* **N-10 Missing matrix row or edited matrix** is cached for 30 s (`loader._TTL_SECONDS`); a deleted row makes `/me` and every check for that role answer an error (fail closed, never open). `sync` repairs both (proved).
* **N-11 Reps see an "Approvals" link** (they hold `approval.request`); prompt 12 lists only Today and Profile for them. Harmless and probably wanted for "My requests"; asserted in `admin.spec.ts` as a documented NOTE.
* **N-12 The Vite dev server has no favicon** (404 in the console on every page); ignored by the console check. Today is no longer a placeholder (later phases), so the "Good to see you" heading is not asserted.
* **N-13 `notify()` counts an "email disabled and in-app disabled" delivery as created.** No effect on users.
* **N-14 Audit rows of failed validation (pydantic 422) and of failed logins are not written** (a failed login has no organisation; PRD 4 says denied attempts are logged; documented in `test_failed_login_is_not_audited_anywhere`).
* **N-15 Team colour (`team.colour.change`) has no endpoint** (key seeded, nothing built; not in the Phase 1 endpoint list).
* **N-16 No endpoint or screen to change notification settings**, so "required types cannot be switched off" is proved at service level only.
* **N-17 Failed-jobs list is capped at 100**, no paging (same pattern as BUG-P1-09).
* **N-18 `GET /users` has no search or paging**; at 1,500 users it takes 120 ms (fine today).

## What passed (important guarantees proved)

* **Isolation between organisations is solid.** For every id route (users, teams, approvals, history, notifications, files, events, failed jobs, password reset, websocket) a second organisation gets the same status and body as for a missing id, no audit row appears in either organisation, nothing changes, and no org id appears in any list response. Team/user ids of another org in create and move calls answer 404. The fuzz test ends with organisation B byte-identical.
* **Authentication.** All 90+ routes except `/health`, `/auth/dev/login` and `/auth/dev/users` answer 401 for no token, garbage, expired. Forged keys, `alg none`, HS512 and edited payloads are refused; role claims in a token are ignored (role comes from the database every request); deactivation, deletion and role change apply on the very next request; a user without a team is refused.
* **Permission matrix**: the 80 keys x 4 roles written by hand equal the Python seed, the database, `guard.evaluate` and `/me`. Fail-closed for unknown ownership. HTTP matrix (5 actions x 8 targets x 4 roles + creates) gives the expected status, a `denied` audit row for every refusal and no change.
* **Reason policy** exactly as PRD 4.2 including whitespace, NBSP, 10,000 characters, emoji, HTML/SQL text (stored verbatim, returned as JSON data, escaped in e-mail and the browser; no script ran in the history dialog).
* **Audit**: one `ok` row per successful write with who/when/old/new/reason/origin, denied rows for refusals, atomic with the change and the outbox event, per-org chain verified; edits, swaps, deletions and inserts located at the right sequence number; the app role cannot UPDATE/DELETE/TRUNCATE and cannot disable the trigger; the superuser is stopped by the trigger; 50 simultaneous writes keep the sequence gapless and the chain valid; the nightly check alerts only active Super admins of the broken org, once. Backup and restore reproduce every table, the chain, the triggers, the grants and the sequences.
* **Outbox**: failed attempts leave no partial effect; replays do not duplicate notifications or e-mails; backoff doubles; the 10th failure parks the event; poison events do not block others; six dispatchers never handle an event twice; a hanging handler does not stop the others; failed jobs list and retry follow the permission and reason rules.
* **Approvals**: routing, every self-approval rule (User/TL never, Admin neither own request nor about own record, Super admin may), 20 simultaneous decisions give one winner and 19 conflicts with the executor run once, cancel only by the requester, requests never expire, reminders once per day, both executors apply exactly once and record both names.
* **Live updates**: refused handshakes, scope and org fan-out, team move / role change / deactivation take effect on the next event, reconnect with `/events` has no gaps or duplicates, 60 sockets get an event in 70 ms.
* **Users and teams**: only a Super admin creates or touches Admin/Super admin; one leader per team (also in the database); the last active Super admin survives 10 concurrent demotions/deactivations and two simultaneous self-demotions; e-mail uniqueness is case-insensitive and survives a 12-way race; team move hook fires once and rolls back with the move.
* **Files**: size, name and path handling is safe (names never leave their key prefix), download is scope-checked every time and follows the owner when they move team, other org / deleted / pending files are 404.
* **Operations**: empty database to head twice; CORS answers only configured origins; the error envelope is identical on every error kind and route; no stack trace or SQL ever appears in a body.
* **Browser**: every login path and message, session end on token removal, deactivation and sign-out, nav per role, forbidden URLs, the whole approval flow with live updates on two browsers, the reason dialog by keyboard, no horizontal scroll on 12 pages and 3 dialogs at 400 px, no console error or unexpected failed request on any page except the failures listed above.

### Performance numbers (ms, in-process, one API process)
`GET /users` 1,500 rows 120; `/teams` 60 teams 44; `/approvals?tab=pending` (100 of 3,000) 70; `/notifications` 17; `/history/user/{id}` 25; `/events?since=0` over 50,000 outbox rows 20; `/me` 7; rename 33-83; `POST /teams` 12; dev login 9. Audit append: 3.0 ms with an empty table vs 4.6 ms with 400,000 newer rows of 40 other orgs (no slowdown). `verify_chain` for 30,000 rows: 5.5 s and 83 MB (linear; 1 M rows would take about 3 min and 2.7 GB, a RISK for the nightly job: stream in batches). The approvals list does one query per row for names (N+1) and `/teams` two per team; fine at this size.

## Open questions for the product owner

1. Should a Team Leader be allowed to rename or edit users directly (BUG-P1-03), or is rename covered by "Request only"?
2. Should a TL be able to request a deactivation and a role change from the UI (BUG-P1-18)?
3. Can a deactivated person be re-added with the same e-mail, or is reactivation the only way (N-6)?
4. PRD matrix says a TL may not reset a password at all; the prompt says everyone resets their own. Confirm self-service for TLs (N-11 / decisions above).
5. PRD matrix says a TL cannot edit an invoice; 4.5 says such requests go to the Admin pool. Which one?
6. Should reps see the Approvals link (N-11)?
7. How should demotion of an Admin to User choose a team (BUG-P1-05)?
8. What retention and anchoring do you want for the audit head hash (BUG-P1-10)?
9. Should the history of a team be visible to Admins only (BUG-P1-17)?
10. Is a rate limit expected at the API or only at the Cognito/WAF layer (N-1)?
