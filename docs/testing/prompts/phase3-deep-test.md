# Phase 3 deep test prompt: "New business pipeline"

First read `docs/testing/COMMON.md` and follow it exactly. Spec: `Relay_CRM_Phase3_Developer_Prompt.md` (the 19
tickets, section 23 "Tests that must pass") and PRD v3.6 sections 4.4 B-I, 5, 7, 9, 10, 11 (Features 2-7), 12-14,
plus `docs/adr/0005-stage-engine.md`. Code: `api/app/modules/pipeline/`, `deals/`, `activities/`, `catalog/`,
`documents/`, `exports/`, `profiles/`, `accounts/timeline.py`.

**Your folders**: `api/tests/deep/phase3/` (add `__init__.py` in `api/tests/deep/` if missing, and in yours),
`web/e2e/phase3/`. Report: `docs/testing/reports/phase3-report.md`. Browser ports: API 8103, web 5203, data folder name
`qa3`, `E2E_REPORT=e2e-report-phase3.json`, `E2E_SCREENS=e2e-results-phase3`. The helper module
`api/tests/modules/journey.py` walks a deal through all stages over HTTP; reuse it.

This phase is what the customers' sales teams live in every day. Anything that lets a rep fake progress, hides a
deal, mis-prices a deal or shows the wrong person's data is severe.

## 1. Pipeline configuration
- Seed correctness: compare version 1 to the PRD table stage by stage (name, probability, entry/final/won flags,
  every exit criterion and its proof type, transitions including Revision to Revision 2 conditional and backward
  links, lost reasons, fit checklist = the 4 confirmed questions). Report every difference.
- Draft/validate: every problem code (`entry_stage`, `no_won_stage`, `bad_transition`, `unreachable`, `no_forward`,
  `bad_entry_check`, `bad_criterion`, `bad_proof`); duplicate keys; slug rules; huge payloads; one draft at a time
  (two admins saving at once); discard; from_version copy.
- Publish: impact preview numbers equal raw SQL; removed stage with deals needs a target; mapping to a missing
  stage; every moved deal gets a `deal_stage_entries` row (`override`, "config move") and an audit row; deals that
  stay keep their snapshot byte for byte; new deals get the new checklist; publish twice, publish a non-draft;
  concurrent publish and move; rollback creates a new version number; versions never deleted; **frozen versions**:
  UPDATE/DELETE/INSERT on stages/transitions/criteria of a published or archived version through SQL must fail
  (also as the superuser), and the app role cannot DELETE `pipeline_versions`.
- Permissions: only Admin/SA (matrix `pipeline.config`), reason on every save for Admin. Custom fields, lost reasons
  and fit checklist lists: validation, ordering, hide-not-delete, unique labels, type immutability.

## 2. Stage engine: exhaustive
- Build the **full transition table** from the PRD and test every (from stage, to stage) pair x role (User owner,
  User not owner, TL of team owner/not owner/own deal, other-team TL, Admin owner/not owner, Super admin) x criteria
  (all ticked / one missing / proof missing) x `override` flag x reason (none/given): assert status code, error
  code, resulting stage, `exit_kind`, overridden criteria, audit row (exactly one, key and reason) and outbox event.
  Specific rules to hit: forward only by the owner for every role (TL/Admin/SA included, also direct API); one stage
  at a time; Revision mandatory (Proposal to Closure impossible without override); Revision 2 reason always required
  and round counter 2, 3, 4; backward needs reason and gives a fresh checklist; override/skip not on the actor's own
  deal for a TL; bulk stage move denied for everyone; Proposal entry needs a proposal document; Revision entry
  needs feedback; Won on entering Onboarding sets `status=won`, `won_at`, emits `deal.won` and `deal.closed`, leaves the
  board; stage entry rows always exactly one open per deal.
- Concurrency: simultaneous moves (same and different targets), tick vs reject, tick vs untick, move vs edit, move
  vs lose, delete vs move, 50 deals moved at once by different owners: no 500, no deadlock, one winner, invariants hold.
- Snapshots: change the pipeline after a deal entered a stage; reopen into the lost-from stage (new snapshot).

## 3. Exit criteria and proof (each proof type, positive and negative)
- `fields` (incl. `any` groups, empty string, whitespace, zero, deleted contact), `activity` (type list, `two_way`,
  `attendees`, `notes`, `future`, `completed`; exactly at "now"; soft-deleted; belongs to another deal), `checklist`
  (no items configured, item added after ticking, item deactivated), `contact_tag`, `deal_lines` (zero lines, deleted
  line, pending discount), `document` (kind, status list, file required, deleted, other deal's file), composite `also`.
- Tick: owner only, proof required (422 `proof_required` with a useful `missing`), already ticked 409, unknown key 404,
  deal not open, hint `proof_ref` pointing at an unrelated activity.
- Untick (reason), verify (TL team, Admin, SA; not out-of-team), reject (reason required; removes the tick; on a passed
  stage sets `review_needed`; owner and TL notified), `review-done`.
- Proof missing: remove proof by every route (delete/edit activity, delete/edit document, empty a field, delete a line,
  untag a contact, change fit checklist items) and prove the flag appears in the same request, notifications fire once,
  moving forward is blocked, and the flag clears when proof returns. Time-based conditions are only judged at tick time
  (a meeting ticked as "future" must not turn into "proof missing" when its time passes: control the clock).

## 4. Deal changes, value and discounts (boundary heavy)
- Value rule thresholds with exact values: change of 4.99 %, 5.00 %, 5.01 %, 9.99 %, 10.00 %, 10.01 %; from value 0;
  negative quantities; quantity 1 and 1,000,000; rounding (half-up) on line totals with fractional discounts
  (`0.01`, `33.33`, `99.99`, `100`); a removal of the only line; per role User / TL / Admin / SA, owner or not.
  Check which permission key and which approval route result, the approval payload, that nothing changes while pending,
  that approving applies once, rejecting/cancelling restores, approving a stale request, and the requester cannot approve.
- Discounts: up to 10 %, above 10 %, below floor with a small discount, exact floor, list price, discount on a line
  that already has one, lowering a discount (no approval), two pending requests on one line, delete a line with a
  pending discount, a product deactivated or repriced meanwhile (existing lines unchanged), floor > list rejected.
- Edit rules: expected close date (first time free; change needs reason; cannot be emptied from Prospecting on),
  pricing fields after Prospecting, lead source after the Lead stage, custom fields types, fit checklist ids, locked
  fields, `expected_version` conflicts, Undo for the new fields (window, other user, changed again).
- A deal can never be left without an open next step; Done always requires the next step; snooze +2 days from past due
  and from future due; snooze count; "They replied"; nudge; TL task assignment scope (own team only); newly assigned
  lead appears on the rep's Today.

## 5. Activities
- Validation (type list, two-way required for call/email/meeting, discussed required, future only for meetings, next
  step required and not in the past), attribution, **24-hour edit window** for User and TL at 23:59/24:00/24:01 (move
  `created_at` with the admin connection), Admin and SA unrestricted for their own, nobody edits another person's
  activity (matrix `activity.edit_others` is denied for all), every version stored and readable, delete rights and reason,
  effect on `last_activity_at`, health and proofs; activities on won deals (kickoff) and not on lost deals.

## 6. Health, Today, board, stage pages, metrics
- Health rules across **IST midnight** and month/year ends (pure function tests and a database run of
  `refresh_health`), precedence (Stale above At risk), Nudge at 2 IST days, Slipped, no SLA by stage; the daily job
  creates one digest per owner per day and is idempotent.
- Today: scope per role (who can open whose), Super admin's Today hidden, sections and counts correct (cross-check
  with raw SQL), done/snooze/reply/nudge permissions.
- Board: seven open columns, Revision and Revision 2 merged with round badge, no weighted forecast anywhere in
  the JSON, counts/values/probabilities cross-checked with SQL, filters (owner, team, source, health, age, search incl.
  `%`, `_`, quotes and SQL text), paging (`offset`/`limit` extremes, order stability under inserts), scope per role and
  Super admin-owned deals hidden from TL/User.
- Stage pages for every stage, Won and Lost: page sizes 10/25/50 only, sort keys (injection attempts), extra columns
  correct per stage (cross-check at least 3 columns per stage with SQL), pagination totals, scope, bulk actions
  (lose, snooze, owner change: per-deal results, partial failures reported, each deal one audit row, permission per deal).
- Stage metrics: win rate, average time, conversion, open count: recompute each with your own SQL on a dataset you
  build (including a deal that went back and forth, a lost deal, a deal skipped by override) and compare.

## 7. Lost, reopen, delete, restore, documents, exports, profiles, timeline
- Lose: reason from the list only, notes, who may, effect on cap and on waiting leads (`deal.closed`), appears on the Lost
  page; reopen to the lost-from stage with a fresh snapshot and clean lost fields; double lose/reopen; reopen when the
  stage was removed. Delete/restore (reason, scope, deleted view, pooled deals regain a Pool entry); nothing erased.
- Documents: kinds x statuses x who may add/edit/terminate/delete (agreement.terminate Admin/SA only; invoice edit by
  TL becomes an approval), file must be uploaded to the same deal, version numbers, effect on proofs.
- Exports: reason policy (Admin needs one, Super admin optional), scope per role (rows equal the visible deals, compare
  with `/deals`), filters including the Revision group, CSV formula injection (`=`, `+`, `-`, `@`, tab), unicode,
  inline vs queued path (patch `INLINE_LIMIT` to 0), download only by the requester, log visibility (TL team, Admin all),
  inactive requester, a very large export.
- Profiles/monitoring/compare: every figure recomputed with SQL (assigned, generated, active vs cap, won, lost + reasons,
  conversion by stage, overdue/due today), period filter edges (`from`/`to`, same day, inverted, future), scope
  (User own, TL team, Admin all, Super admin hidden from TL), reassign from the monitoring view works.
- Account timeline: bands match stage entries exactly, events ordered, ownership and milestone events present, locked
  deals excluded, engagement strip counts equal SQL.

## 8. Cross-cutting
- For **every** Phase 3 endpoint (enumerate from OpenAPI): role matrix, two-org isolation, 401 unauthenticated, extra
  fields, injection in every text field and query parameter, pagination limits, large payloads.
- **Model-based fuzz**: seeded random sequences (at least 500 actions per run, three seeds) of create lead, assign, log
  activity, edit fields, tick/untick/verify/reject, move (legal and illegal), lines/discounts, approvals decided,
  documents, lose/reopen/delete/restore, bulk actions, config publish, by random roles. After each run assert the
  invariants: exactly one open stage entry per non-pooled live deal; `stage_key` equals that entry's stage; `status` and
  `won_at`/`lost_at` consistent; `value_paise` = sum of live line totals; every open deal has exactly one open next step;
  no `deal_criteria` without an entry; criteria only for keys in the entry snapshot; proof_missing flags equal a fresh
  recomputation; each ok write has exactly one audit row; the audit chain verifies; no 500 responses at any point.
- Performance: run `api/scripts/loadtest.py` as documented (2,000 deals, 50 users, `--seconds 60`) twice and report p50/p95;
  profile the deal page (count SQL statements per request, flag anything above 40), board, stage page with 2,000 rows,
  `refresh_health` on 5,000 deals, publish with 2,000 deals to move. Targets: board 1.5 s, deal page 1 s, move 300 ms.

## 9. Browser (Playwright, folder `web/e2e/phase3/`)
Walk a **real deal from Lead to Won through the screens** as the rep, with the Team Leader verifying and the Admin
approving, using the UI for every step (log activity, tick, move, edit fields, add product line with a discount that
needs approval, add documents with a file upload, mark the decision maker). Also: checklist states and the messages for
missing proof; backward move and override dialogs; Revision 2; Undo bar; proof-missing banner after deleting an
activity; Lost and Reopen; board (filters, load more, drag a card forward, drag backward asks for a reason, blocked drop
snaps back and lists missing criteria, keyboard move button); stage pages (sort, paginate, bulk lose and snooze with a
partial failure); Today (Done requires the next step, snooze, replied, TL assigns a task); profile, monitoring,
compare; exports page (reason, status, download link); account page timeline (band view and list view); Admin pipeline
editor (start draft, edit stage and criterion, check, impact preview with removed stage and mapping, publish, rollback),
catalog (rupee inputs, floor check), settings lists (lost reasons, fit checklist, custom fields). Phone width 400 px,
console errors, no horizontal scroll, labels, focus order, dark and light theme readable.
