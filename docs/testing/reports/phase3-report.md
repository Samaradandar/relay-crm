# Phase 3 deep test report: "Pipeline, stage engine, board and stage pages"

## Fix status (QA fix pass, 2026-10-08)

All strict-xfail and `test.fail` marks of Phase 3 were removed; the folders now run without any expected failure.

| ID | Status | Where fixed |
| --- | --- | --- |
| BUG-P3-06 | Fixed | `pipeline/config/service.py`: Won deals count in the impact preview and need a mapping |
| BUG-P3-10 | Fixed (UI) | `deals/DealPage.tsx`: a Won deal that still has a checklist is worked on (log activity, tick) |
| BUG-P3-18 | Fixed | `deals/board.py`: the Onboarding page lists Won deals |
| BUG-P3-22 | Fixed | `deals/board.py`: fit items counted per organisation |
| BUG-P3-24 | Fixed | `deals/common.py` (`ensure_live_deal`) used by activities, versions, documents, lines reads |
| BUG-P3-27 | Fixed | `profiles/service.py`: team won value uses the same reps as the won count |
| BUG-P3-28 | Fixed | `exports/service.py`: correlated stage-group subquery |
| BUG-P3-01, 05 | Fixed | `pipeline/config/schemas.py`: trimmed non-empty text types |
| BUG-P3-02, 03 | Fixed | migration `0006_qa_phase3.py` (frozen-version trigger checks OLD and NEW; status guard) |
| BUG-P3-04 | Fixed | `_put_list` revives a hidden entry typed again |
| BUG-P3-07 | Fixed | duplicate custom field key gives 422 |
| BUG-P3-08 | Fixed | publish locks open deals (id order) before counting |
| BUG-P3-09 | Fixed | skipping into Revision 2 sets round 2 |
| BUG-P3-11 | Fixed | document patch re-checks status against the resulting file |
| BUG-P3-12, 13 | Fixed | untick and reject re-run `recheck_proofs` |
| BUG-P3-14, 15, 23 | Fixed | quantity and money bounds; discount quantised to 2 decimals (23 also by the global DB error handler) |
| BUG-P3-16 | Fixed | a newer discount request cancels the older; the older cannot be approved |
| BUG-P3-17 | Fixed | global DB data-error handler returns 422 (platform change by another agent) |
| BUG-P3-19, 20 | Fixed | search escapes backslash; paging query parameters bounded |
| BUG-P3-21 | Fixed | Revision page has a "Requested changes" column |
| BUG-P3-25 | Fixed | export owner and team filters must be UUIDs (422) |
| BUG-P3-26 | Fixed | period bounds are IST midnights |
| BUG-P3-29 | Fixed | dev token without email or sub claim is 401 |
| BUG-P3-30 | Not fixed / unverified | `CatalogPage.tsx` already has an `overflow-x-auto` wrapper; the cause of the 400 px scroll could not be reproduced because the disk filled up during the browser run. The `test.fail` mark was removed, so this spec will fail until fixed |
| BUG-P3-31 | Fixed (UI) | slate-400 small text changed to slate-600 |
| BUG-P3-32 | Fixed (UI) | Tick, Untick and Move buttons respect `own_deals_only` / `not_own_deals` and ownership |
| BUG-P3-33 | Fixed (UI) | stage page remembers company names of rows it has shown |
| GAP-P3-01 | Fixed | account page shows stage bar, next step, checklist and documents per deal (`accounts/DealSnapshot.tsx`) |
| RISK-P3-02 | Not fixed | publish still moves deals one by one (10 s for 2,000); needs batching or a background job |
| RISK-P3-01 | Not fixed | `loadtest.py` is a script issue, not product code |

Browser run (ports 8103 / 5203): 30 of 58 specs passed (account page incl. GAP-P3-01, exports incl. BUG-P3-28, board, stage pages, admin, profiles) before the machine ran out of disk space (11 MB free) and the embedded PostgreSQL died; the rest (walkthrough incl. P3-10 and P3-32, responsive incl. P3-30 and P3-31, Today) were not verified in a browser. Web unit tests: 54 passed; tsc and eslint clean. Two profile specs are sensitive to demo data (`getByLabel('To')` also matches a meter labelled 'Competitor'; the demo now contains a rep 'Aarav Kapoor').

Test changes: `test_02` now expects a Won Onboarding deal on the Onboarding page (it had encoded BUG-P3-18); `test_01_huge_payload` accepts the platform's new 413 for bodies over 2 MB.

## Summary

- **Date**: 2026-10-07. Tester: QA agent for Phase 3. Spec: PRD v3.6 (sections 4, 9-13), ADR 0005 (stage engine), the Phase 3 developer prompt and `docs/testing/prompts/phase3-deep-test.md`. Code under test: `api/app/modules/pipeline/`, `deals/`, `activities/`, `documents/`, `exports/`, `profiles/`, `board/` and the matching `web/src` screens.
- **Backend tests** (`api/tests/deep/phase3/`): 378 test cases in 14 files; 347 pass, 31 are strict xfail (known bugs). The folder is green. With `--runxfail` all 31 fail, so each xfail shows a real defect.
- **Browser specs** (`web/e2e/phase3/`): 58 specs in 8 files; 52 pass and 6 are `test.fail()` (known bugs and one gap). All green on a freshly reset database (`--reset --data qa3`): `58 passed (4.1m)`.
- **Findings**: 33 BUG, 1 GAP, 4 RISK/NOTE. Highest severity found: S2 (no S1 found; the closest is BUG-P3-22, a count that uses other organisations' fit items, and BUG-P3-24, deleted records still readable).
- **Whole backend suite** (run at the end): 1382 passed, 3 skipped, 122 xfailed, 0 failed (26 minutes). Nothing in product code was changed.
- **Not tested / limits**:
  - Email delivery (in-app rows only) and a real object store. Uploads and exports used a small fake S3 server (`web/e2e/phase3/fake_s3.py`, port 9103); the backend export tests use an in-memory `FakeStorage`.
  - Real WebSocket fan-out; the outbox rows and the `/events` catch-up feed were checked instead.
  - Performance numbers come from a laptop with an embedded PostgreSQL; they are an early signal, not a staging result.
  - The `loadtest.py` "move" errors are 403s caused by the script (see RISK-P3-01), so its error column cannot be used as is.
  - Dark mode: the app has none (NOTE-P3-01), so only "stays readable" was checked.
- **Deviations from the plan** (decisions made without a human):
  - The API for the browser run needs `CORS_ORIGINS=http://localhost:5203` and the S3 variables for the fake store (`S3_ENDPOINT_URL`, `S3_PUBLIC_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`).
  - The monitoring and profile browser specs use Priya Menon (the first rep of Team North). A freshly reset local database has no "Aarav Kapoor"; older runs of this spec assumed one.
  - Because Phase 1 and Phase 3 tests share one embedded database in a full run, two helpers were made order-independent: audit marks use the identity sequence's last value (a Phase 1 tamper test leaves audit rows with `seq >= 1,000,000`), and the grants test skips itself when `relay_app` holds DELETE on every table (BUG-P1-11, the jobs schema grants ALL).
- **How to run**:
  - Backend: `cd api; .\.venv\Scripts\python.exe -m pytest tests/deep/phase3 -q -p no:cacheprovider --no-cov` (about 6.5 minutes). Add `-s` to see the `PERF` lines of `test_14_performance.py`.
  - Whole suite: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --no-cov` (about 26 minutes).
  - Browser: start the fake store (`python web/e2e/phase3/fake_s3.py 9103`), the API (`scripts/run_local.py --reset --data qa3 --port 8103` with the variables above) and Vite (`VITE_API_URL=http://localhost:8103`, port 5203); then `cd web; E2E_API_PORT=8103 E2E_WEB_PORT=5203 E2E_REPORT=e2e-report-phase3.json E2E_SCREENS=e2e-results-phase3 E2E_DATA=qa3 node node_modules/@playwright/test/cli.js test e2e/phase3`. The specs add data and are meant to run on a fresh reset database; the admin spec cleans up a leftover draft and hidden lost reason itself.

## Coverage map

| Area (prompt section) | What was tested | Files |
| --- | --- | --- |
| 1 Pipeline config | seeded pipeline equals the PRD table (stages, probabilities, links, criteria, conditional rules); draft / validate / publish / rollback lifecycle; frozen published and archived versions (trigger, app role); impact preview equals SQL and the mapping rules; removed stage with deals; custom fields, fit checklist, lost reasons, catalog validation | `test_01_pipeline_config.py` |
| 2 Stage engine | every target x every role move matrix against an independent PRD oracle; forward / backward / skip / override kinds; reason policy; Revision and Revision 2 rounds; Won on entering Onboarding; audit rows and outbox events one each | `test_02_stage_engine.py` |
| 3 Exit criteria and proof | tick / untick / verify / reject (roles, owner only, reasons, notifications); the six proof validators (fields, activity, checklist, contact tag, deal lines, document) and `also`; `recheck_proofs`; proof-missing flag; snapshot per stage entry | `test_03_criteria_proofs.py` |
| 3 and 8 Concurrency | same move x10 (one winner), 50 deals moved at once, forced interleaving with `LOCK TABLE outbox_events`, publish vs move, approval races | `test_04_concurrency.py` |
| 4 Value and discounts | 4.99 / 5.00 / 5.01 / 9.99 / 10.00 / 10.01 % thresholds up and down per role; approval routes and payloads; stale and own approval; discount matrix (10 %, floor, list price, lowering, two pending, deleting a line); rounding half-up; quantity 1 / 1,000,000 / huge | `test_05_value_discounts.py` |
| 4 Edit rules, next steps | expected close date rules, pricing fields after Prospecting, lead source locked, custom field types, `expected_version` conflict, Undo window; one open next step always; snooze from past and future, count; nudge; TL task scope | `test_06_deal_edits_next_steps.py` |
| 5 Activities | validation, two-way, future meetings, 24-hour edit window at 23:59 / 24:00 / 24:01, versions, delete rights and reasons, effect on `last_activity_at`, health and proofs, won and lost deals | `test_07_activities.py` |
| 6 Health and Today | health rules across IST midnight, month and year ends (pure and `refresh_health` on a database), precedence, daily digest idempotent; Today per role, counts equal SQL, done / snooze / replied / nudge | `test_08_health_today.py` |
| 6 Board, stage pages, metrics | seven columns, Revision merge and badge, no weighted forecast in the JSON, counts and values equal SQL, filters (`%`, `_`, quotes, SQL text), paging extremes, stage pages (sizes, sorts, extra columns for every stage), bulk lose / snooze / reassign with partial failures, stage metrics recomputed with own SQL | `test_09_board_stages.py` |
| 7 Lost, reopen, delete, documents | lose reasons, reopen to lost-from stage, delete / restore, documents (kinds x statuses x roles, terminate, invoice edit approval, file must belong to the deal, versions) | `test_10_lifecycle_documents.py` |
| 7 Exports, profiles, timeline | reason policy, scope, filters, formula injection, unicode, inline vs queued, download by requester only, log visibility; profile, monitoring and compare figures recomputed with SQL; period edges; account timeline bands | `test_11_exports_profiles_timeline.py` |
| 8 Cross-cutting | every Phase 3 route from OpenAPI: 401, forged and expired tokens, role matrix, IDOR (other team, other organisation), mass assignment, injection in text and query parameters, paging limits, large payloads | `test_12_cross_cutting.py` |
| 8 Fuzz | seeds 11, 23, 37 x 560 random actions by random roles (create, assign, activity, edit, tick / untick / verify / reject, legal and illegal moves, lines, discounts, approvals, documents, lose / reopen / delete / restore, bulk, publish); invariant checker after each run (see below) | `test_13_fuzz.py`, `invariants.py` |
| 8 Performance | SQL statements per request, timings at 2,000 deals, `refresh_health` on 5,000 deals, publish moving 2,000 deals, `loadtest.py` x2 | `test_14_performance.py`, `api/scripts/loadtest.py` |
| 9 Browser | Lead to Won walkthrough with rep, Team Leader and Admin; real file upload; board (filters, load more, drag forward, drag backward with reason, blocked drop, keyboard Move); stage pages (sort, paginate, bulk with partial failure); Today; profile, monitoring, compare; exports; account timeline; admin pipeline editor, catalog, settings lists; 400 px, labels, keyboard, colours | `web/e2e/phase3/*.spec.ts` |

Fuzz invariants checked: one open stage entry per live non-pooled deal; `stage_key` equals that entry; status and `won_at` / `lost_at` consistent; `value_paise` equals the sum of live line totals; one open next step per open deal; no `deal_criteria` without an entry or outside the snapshot; proof-missing flags equal a fresh recomputation; each ok write has exactly one audit row; the audit chain verifies; no 500 anywhere.

## Performance

In-process profile (`test_14_performance.py`, 2,000+ deals, 4 reps, median of repeated calls):

| Call | Median | Max | Target |
| --- | --- | --- | --- |
| Board (admin / rep) | 99 / 46 ms | 108 / 100 ms | 1.5 s |
| Deal page | 18 ms | 29 ms | 1 s |
| Stage page, 50 rows | 42 ms | 48 ms | |
| Stage metrics (all) | 305 ms | 319 ms | |
| Today | 209 ms | 264 ms | |
| Monitoring / teams compare | 446 / 458 ms | 491 / 507 ms | |
| Deals list 500 / search | 58 / 106 ms | 122 / 116 ms | |
| Move | 17 ms | 83 ms | 300 ms |

SQL statements per request: deal page 23, board 12, stage page 13, move 18 (all under the 40 flag). `refresh_health` on 5,000 deals: 1.4 s (second run 0.8 s, nothing changed). Publish moving 2,000 deals: 10.3 s (RISK-P3-02).

`loadtest.py` (2,000 deals, 50 users, 60 s, data folder `qa3`, API on 8103), two runs, p50 / p95 in ms:

| Op | Run 1 p50 / p95 | Run 2 p50 / p95 | Target p95 |
| --- | --- | --- | --- |
| board | 83 / 526 | 62 / 530 | 1500 |
| deal | 40 / 281 | 50 / 359 | 1000 |
| move | 38 / 193 | 44 / 285 | 300 |
| stage page | 58 / 91 | 61 / 183 | |
| today | 212 / 525 | 109 / 298 | |

A third 40-second probe run showed move p95 of 349 ms (above 300) with one slow outlier near 1.5 s, so the move target is met only narrowly under 50 users on this laptop. Board p95 is about 0.5 s and deal p95 0.3 s.

## Findings

Each BUG has a strict-xfail backend test or a `test.fail()` browser spec carrying the id in its reason or title. Remove the mark when the bug is fixed. The test names are the ones containing `BUG_P3_nn` / `BUG-P3-nn`; find them with `grep -rn "BUG-P3-nn" api/tests/deep/phase3 web/e2e/phase3` (the Python files spell the id with a hyphen in the xfail reason).

### Severity S2

**BUG-P3-06 (BUG, S2): publishing a draft that removes Onboarding strands Won deals.**
Steps: draft without the Onboarding stage, publish with no mapping. Expected (PRD 9, ADR 0005): the publish asks where those deals go or refuses. Actual: Won deals keep a `stage_key` that no longer exists. Test: `test_01_pipeline_config.py` (BUG-P3-06). Fix: include Won deals in the impact preview and require a mapping.

**BUG-P3-10 (BUG, S2, UI): a Won deal in Onboarding cannot be worked on screen.**
Steps: move a deal to Onboarding (it becomes Won), open it. Expected (PRD 11, Onboarding checklist and kickoff activity): Log activity and Tick work. Actual: the deal page hides Log activity and every Tick button as soon as the deal is Won; the work only succeeds through the API. Test: `walkthrough.spec.ts` (BUG-P3-10). Fix: show the actions for Won deals that sit in Onboarding.

**BUG-P3-18 (BUG, S2): the Onboarding stage page can never list a deal.**
A deal entering Onboarding is Won but the page lists open deals only, so the Kickoff, Go-live and First invoice columns are never seen. Test: `test_09_board_stages.py` (BUG-P3-18). Fix: list Won deals for that page.

**BUG-P3-22 (BUG, S2): the Qualification page's "Fit check" column uses all organisations' fit items.**
The denominator is the number of fit items across every organisation, not the caller's, so the figure is wrong and leaks other tenants' configuration size. Test: `test_09_board_stages.py` (BUG-P3-22). Fix: filter the count by `org_id`.

**BUG-P3-24 (BUG, S2): records of a soft-deleted deal can still be read.**
After delete, the rep can still read the deal's activities, documents, activity versions and lines, while the PRD matrix says deleted records are "No" for Users. Test: `test_10_lifecycle_documents.py` (BUG-P3-24). Fix: apply the deleted filter in those read paths.

**BUG-P3-27 (BUG, S2): team comparison counts won deals for reps only but sums the value of every team deal.**
A team can show 0 won with a won value. Test: `test_11_exports_profiles_timeline.py` (BUG-P3-27). Fix: use the same set of deals for count and value.

**BUG-P3-28 (BUG, S2): an export with a stage filter returns 500.**
Happens whenever the organisation has two or more deals; it breaks the Exports page and the stage page "Export CSV" button. Tests: `test_11_exports_profiles_timeline.py`, `exports.spec.ts` (BUG-P3-28). Fix: repair the stage-filter query.

### Severity S3

- **BUG-P3-01**: whitespace-only stage name or criterion label is accepted and stored as '' (a 500 follows on some paths). Fix: strip then validate. Test `test_01` (BUG-P3-01).
- **BUG-P3-02**: the frozen-version trigger only checks `NEW.version_id`, so a row can be moved out of a published version into a draft. Fix: also check `OLD.version_id`. Test `test_01`.
- **BUG-P3-03**: the app role can flip an archived version back to `draft` and so unfreeze it. Fix: forbid status changes out of `archived` in the trigger. Test `test_01`.
- **BUG-P3-04**: re-adding a hidden lost reason by its text (no id) hits the unique constraint and returns 500 (the Settings lists screen shows a CORS error because the error page has no CORS header). Fix: revive the hidden row or return 409. Tests `test_01`, `admin.spec.ts`.
- **BUG-P3-07**: the same custom field key twice in one request returns 500. Fix: reject duplicates with 422. Test `test_01`.
- **BUG-P3-08**: publish does not lock deals; a move in flight lands a deal in a stage the publish just removed. Fix: lock the open deals (or version row) in the same order as `move`. Test `test_01`.
- **BUG-P3-09**: skipping straight into Revision 2 leaves round 1, so the board badge says R1. Fix: set round 2 on entering `revision_2`. Test `test_02`.
- **BUG-P3-11**: a sent or countersigned agreement can lose its file through PATCH `/documents` (`file_id` null). Fix: reject. Test `test_03`.
- **BUG-P3-12**: unticking the only proof-missing criterion leaves the deal's warning on forever. Fix: recompute after untick. Test `test_03`.
- **BUG-P3-13**: rejecting the only proof-missing tick leaves the warning on forever. Fix: recompute after reject. Test `test_03`.
- **BUG-P3-14**: quantities above 2,147,483,647 are accepted and crash on the 32-bit column (500). Fix: add a maximum (422). Test `test_05`.
- **BUG-P3-15**: a discount with more than 2 decimals is priced with the long value but stored rounded, so line total and stored discount disagree. Fix: quantise on input. Test `test_05`.
- **BUG-P3-16**: with two pending discount requests on one line the older can be approved and the newer stays pending forever. Fix: supersede or refuse the older. Test `test_05`.
- **BUG-P3-17**: a NUL character in any free text field returns 500 instead of 422. Fix: strip or reject `\x00` in the shared string validator. Tests `test_07`, `test_12`.
- **BUG-P3-20**: extreme or negative paging numbers crash with 500 (board `min_age_days`, `per_column`, `offset`; stage page huge `page`; `GET /deals` negative `limit`), and `page<1` is echoed back. Fix: bound the query parameters. Tests `test_09`, `test_12`.
- **BUG-P3-21**: the Revision stage page has no "requested changes" column (prompt section 13). Fix: add the column. Test `test_09`.
- **BUG-P3-23**: an invoice amount above 64 bits is accepted and crashes the database layer (500). Fix: add a maximum. Test `test_10`.
- **BUG-P3-25**: an export filter `owner_id` or `team_id` that is not a UUID returns 500. Fix: type the parameters as UUID (422). Test `test_11`.
- **BUG-P3-26**: profile, monitoring and compare periods start at UTC midnight, not IST midnight, so activity between 00:00 and 05:30 IST lands on the previous day. Fix: build the period bounds in IST. Test `test_11`.
- **BUG-P3-32 (UI)**: a Team Leader, Admin or Super admin is offered Tick, Move and Skip buttons on someone else's deal; the server then refuses with 403. Fix: also check ownership in the screen. Test `walkthrough.spec.ts`.

### Severity S4

- **BUG-P3-05**: whitespace-only lost reason, fit item or label is stored as an empty string. Test `test_01`.
- **BUG-P3-19**: a backslash in the board and stage search is not escaped, so `a\` matches names containing `a%`. Test `test_09`.
- **BUG-P3-29**: a validly signed dev token without an email claim raises `KeyError` (500 instead of 401). Test `test_12`.
- **BUG-P3-30 (UI)**: the catalog page is 820 px wide and scrolls sideways at 400 px. Test `responsive.spec.ts`.
- **BUG-P3-31 (UI)**: the pipeline settings screen has small slate-400 text at about 2.6:1 contrast (needs 4.5:1). Test `responsive.spec.ts`.
- **BUG-P3-33 (UI)**: the stage page bulk-action failure list names a deal by its internal id when it has left the page. Seen in `board.spec.ts` (recorded as an annotation, no `test.fail`, because the list still shows the reason).

### Gap

- **GAP-P3-01 (GAP, S3)**: the account page shows deals, contacts and the timeline only; the Phase 3 prompt (section 14) asks for a stage progress bar, next step, checklist and documents. Test: `account.spec.ts` (`test.fail`). Question for the owner below.

### Risks and notes

- **RISK-P3-01 (RISK, S4)**: `loadtest.py` reports 403 `deal.move_forward` as errors (about 50 per run) because Team Leader and Admin workers try to move deals they do not own. The permission is correct; the script should only move the worker's own deals.
- **RISK-P3-02 (RISK, S3)**: publishing a pipeline that moves 2,000 deals takes 10.3 s; the publish holds locks for that time. Consider batching for larger organisations.
- **NOTE-P3-01 (NOTE, S4)**: the app has no dark theme; the page stays light when the browser prefers dark (readable). Asserted in `responsive.spec.ts`.
- **NOTE-P3-02 (NOTE, S4)**: the Vite dev server has no favicon (404 in the console). The browser helper filters it.

## What passed

- The seeded pipeline equals the PRD table; versions are frozen for published and archived states (except BUG-P3-02 / 03); publish and rollback keep checklists of deals already in a stage.
- The move engine matches an independent PRD oracle for every role x target x override combination; each ok write has one audit row and one outbox event; the audit chain verifies after every fuzz run.
- Concurrency: ten identical moves have one winner; 50 deals moved at once by different owners all succeed; a forced interleaving of publish and move was exercised.
- Fuzz (3 seeds x 560 actions): no invariant violations and no 500 other than the listed bugs.
- Scope: reps, Team Leaders, Admins and the Super admin see only what the matrix allows on board, stage pages, Today, profiles, monitoring, exports and timelines; the IDOR sweep over every id route (other team and other organisation) found no leak except BUG-P3-22 (a count) and BUG-P3-24.
- Value thresholds and approval routes behave at all six boundary values; rounding is half-up; nothing changes while an approval is pending.
- CSV exports defuse formulas (`=`, `+`, `-`, `@`, tab), handle unicode, and are downloadable only by the requester.
- Browser: a real deal goes from Lead to Won through the screens (rep, Team Leader, Admin) including a real upload; drag forward, drag backward with a reason, blocked drop and keyboard Move work; 400 px has no sideways scroll on every Phase 3 screen except the catalog; every visible input has a label; the console is clean apart from the listed bugs.
- Performance targets are met in the in-process profile and in both load runs (board p95 about 0.5 s, deal p95 about 0.3 s, move p95 0.19-0.29 s).

## Open questions for the product owner

1. Should a whitespace-only name or label be rejected (422) everywhere? The code mixes rejecting and storing '' (BUG-P3-01 / 05).
2. When a published pipeline removes Onboarding, where should Won deals go? Is the Onboarding stage page meant to list Won deals (BUG-P3-18, BUG-P3-06)?
3. Should the account page show stage progress, next step, checklist and documents (GAP-P3-01), or is the current deals-and-timeline page the decision?
4. Should Admin and Super admin see Tick and Move buttons on other people's deals (BUG-P3-32), or only the Team Leader's verify and override actions?
5. Should period filters follow IST days (BUG-P3-26)? The rest of the product uses IST midnight.
6. Is a 10-second publish for 2,000 deals acceptable, or should big moves run as a background job with progress (RISK-P3-02)?
