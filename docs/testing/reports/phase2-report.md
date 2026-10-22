# Phase 2 deep test report: "Lead Pool"

## Summary

- **Date**: 2026-10-07. Tester: QA agent for Phase 2. Spec: PRD v3.6 (Feature 1, 4.4 B and F, 4.6, 6, 11), the Phase 2 developer prompt, code in `api/app/modules/leads/`, `accounts/`, `leads/assignment/`, `leads/transfers.py`.
- **Backend tests** (`api/tests/deep/phase2/`): 466 written; 406 pass, 59 are strict xfail (known bugs), 1 skipped (pg_trgm cannot be enabled on the embedded server). The whole folder is green; the full backend suite was run once at the end (see "Full suite" below).
- **Browser specs** (`web/e2e/phase2/`): 45 specs in 5 files; 34 pass, 11 are `test.fail()` (known bugs and gaps). All green.
- **Findings**: 41 BUG, 3 GAP, 6 RISK/NOTE (blocks below). Highest severity: S2 (no S1 found; the closest, BUG-P2-18, is an organisation-isolation lapse that cannot be reached without knowing a foreign team id).
- **Not tested / limits**:
  - `pg_trgm` is not available in the embedded PostgreSQL, so the SQL trigram path was never exercised and the two paths could not be compared; the Python path was timed instead.
  - Email delivery (in-app rows only), real WebSocket fan-out beyond the Pool live-update browser test (the catch-up `/events` feed and `visible_to` were tested instead), timing side channels in the duplicate check (not measurable reliably in-process).
  - Nothing was changed in product code. One deviation: the API for the browser run was started with `CORS_ORIGINS=http://localhost:5202` because the default only allows port 5173; data folder `qa2` was re-created once after a failed reset.
- **How to run**: `cd api; .\.venv\Scripts\python.exe -m pytest tests/deep/phase2 -q -p no:cacheprovider --no-cov` (about 5.5 minutes). Browser: `cd web; node node_modules/@playwright/test/cli.js test e2e/phase2 --config playwright.config.ts` with `E2E_API_PORT=8102 E2E_WEB_PORT=5202`, an API started with `--demo` and `CORS_ORIGINS` set to the web origin.

## Coverage map

| Area (prompt section) | What was tested | Files |
| --- | --- | --- |
| 1 Creating a lead | company 1/200/201/whitespace/Devanagari/Tamil/emoji/HTML/SQL/CSV/NUL; 8 valid and 7 invalid phone forms; email forms (`.local`, case, `+tag`, IDN, no TLD, 255+, dots); 8 valid and many invalid website forms; source list; due date yesterday/today/tomorrow and the IST midnight boundary with a controlled clock (7 cases); future first-contact date; extra JSON fields (9); content types, huge and deep bodies; 401/forged tokens; role matrix; audit rows and the single `deal.pooled` event; atomicity (3 injected failures: pipeline state, `start_deal`, audit); inline engine failure with outbox recovery; races for same domain and same phone | `test_01_create_lead.py` |
| 2 Duplicates | domain/phone/email variants block; plus-tag; similar names (26 measured pairs); threshold; create-anyway and override by role with audit and `duplicate_overrides`; flagging to Admins once; leak tests (4 match types x 3 viewers, same-team non-owner, SA-owned owner hiding, header/body UUID scan, shape equality, `/accounts` search, timeline, deal read); locked rows; merge (roles, effects, twice, into itself, cycle, merged account, cross-org, two merges racing, opposite directions) | `test_02_duplicates.py` |
| 2 Duplicate performance | 1,000 / 5,000 / 20,000 accounts: check, create, search; event-loop stall; pg_trgm comparison (skipped) | `test_03_dup_perf.py`, `perf_results.json` |
| 3 Assignment engine | priority order, inactive, one/several/no fields, case and trimming, ties, team/group/user targets, empty and capped targets, no default team; inactive/TL/Admin never round-robin; rep in two groups; caps 0/1/N/override/lower/raise, lost/deleted/won free the cap; 200 leads over 4 reps; rotation with removal and addition; pointer per target; 100 concurrent leads x3; cap boundary under concurrency; idempotency (engine, retry, outbox replay); `pool_retry`; waiting reason, notify-once across every retry trigger; manual hold; settings API (required team, references, ranges, soft delete, roles, reasons, audit, read-only test tool, concurrent saves) | `test_04_engine.py` |
| 4 Pool and ownership | visibility per role, 8 filter combinations, hostile params, ordering, 200-row cut, distribution cross-checked with raw SQL for 4 roles, manual assign (roles, scope, reason, cap warning, bad states, bad targets, races), reassign, bulk (one rep, spread, zero deals, 400 deals, inactive source, permissions), direct team move, team move of a rep (move / return to pool) | `test_05_pool_assign.py` |
| 5 Transfers | request roles, give and take rules, 10 simultaneous requests, give vs take race, rejected/cancelled does not block, comments (who, limits, closed), deciding (roles, reasons, double approval race), approval effects and immediate revocation (REST, lists, events feed), notifications, stale requests (5 kinds), lookup (roles, wildcards, injection, limits, other org), list visibility | `test_06_transfers.py` |
| 6 Edit and Undo | editable and locked fields (15), never-empty, version conflicts and race, no-op edit, source-after-Lead permission, Undo (own, others, twice, wrong ids, window at 0/9/10/10.5 s with a controlled clock and by editing `audit_log.occurred_at`, later edit, multi-field) | `test_07_edit_undo.py` |
| 7 Companies, contacts, search | account and contact CRUD by role, reasons, validation, merged/deleted hidden, search scope and fuzz typos, hostile input, 25-row cap, no hard delete, timeline roles | `test_08_accounts.py` |
| 8 Cross-cutting | every Phase 2 route read from OpenAPI: 401 and forged token (27 routes); role matrix for GET and write endpoints; second organisation against 23 id-bearing calls x 4 actors; extra fields; injection stored verbatim; history limits; malformed bodies; NUL | `test_09_cross_cutting.py` |
| 8 Fuzz and invariants | 300 random actions x 3 seeds (create, assign, reassign, bulk, transfer, decide, comment, merge, edit, undo, settings, deactivate, team move, lose, delete, restore, move-team); 18 invariants in `support.invariant_violations` plus audit-chain verification | `test_10_fuzz.py`, `support.py` |
| 9 Browser | New lead form (labels, validation next to fields, duplicate panel states, Create anyway, override, out-of-scope panel, keyboard only, 400 px), Lead Pool (reasons, assign dialog, distribution vs API, bulk dialog, live update across two browsers, keyboard, 400 px), Transfers (lookup, take, comment, approval, revocation, give), Companies (search, locked rows, merge dialog, 400 px), Settings, Assignment (rule, group, cap, save with reason, test tool, routing, labels, 400 px), console errors and failed calls on every flow | `new-lead.spec.ts`, `pool.spec.ts`, `transfers.spec.ts`, `accounts.spec.ts`, `settings.spec.ts`, `support.ts` |

## Fix status (developer pass, 2026-10-08)

Every finding below was worked on in product code; the strict-xfail and `test.fail` marks of fixed bugs were removed, so those tests now pass normally. Status values: **Fixed** (test passes without the mark), **Fixed (decision)** (fixed in line with an owner decision) and **Open** (not fixed, reason given).

| ID | Status | What was done |
| --- | --- | --- |
| BUG-P2-15 | Fixed | Merge writes the source first (it leaves the unique-domain index), then the survivor adopts the domain. |
| BUG-P2-18 | Fixed | Rule targets and group teams are looked up inside the organisation; ids of other organisations are never reused. |
| BUG-P2-20 | Fixed | `move_deal_to_team` refuses lost, won and deleted deals (409). |
| BUG-P2-22 | Fixed | Moving or giving a lead that waits in the Pool closes the old entry (`left_reason = moved`, migration 0005) before the new one opens. |
| BUG-P2-27 | Fixed | Account scope follows the deals (PRD 4.6); the creator keeps an account only while it has no live deal. Old tests that documented the `created_by` widening were updated. |
| BUG-P2-37 | Fixed | New lead form goes to the deal only if the creator can open it; otherwise it stays and shows a confirmation panel. |
| BUG-P2-40 | Fixed | `GET /teams` lists every team name for a Team Leader (leader and head-count only for their own team). |
| RISK-P2-01 | Fixed | `accounts.name_grams` (trigram array, GIN index, migration 0005) prefilters candidates with an exact prefix-filter plus size bound; scoring runs in a worker thread. Works without `pg_trgm`. At 20,000 accounts the no-match check fell from 529 ms to about 120 ms and a parallel `/me` call from 3.3 s to under 100 ms (embedded test server, no autovacuum). |
| BUG-P2-01, -28 | Fixed | Names and next steps are trimmed and cannot be blank (schema, also on edit). |
| BUG-P2-02, -03, -04 | Fixed | Phone extensions, emails over 254 characters and empty domain labels are refused. |
| BUG-P2-05, -30 | Fixed | Only http(s) addresses with a dotted, lettered host are accepted (no IPs, schemes, spaces, login part); the same rule on account create and patch. |
| BUG-P2-06 | Fixed | First contact date in the future is refused. |
| BUG-P2-07 | Fixed | Industry, city, role: 200 characters; phone and email 320; reason 2000. |
| BUG-P2-08, -09 | Fixed | Lead and account creation take a per-organisation advisory lock around check-and-insert. |
| BUG-P2-10, -11 | Fixed | Name matching drops legal-form words (Pvt, Private, Ltd, Limited, LLP, LLC, Inc, Corp ...) and compares Indic words by akshara units. `similarity()` itself is unchanged (pg_trgm parity test). |
| BUG-P2-12 | Fixed | Out-of-scope matches carry company, owner and `in_scope` only (no team name). |
| BUG-P2-13 | Fixed | A merged account keeps its domain when the survivor has its own, and the duplicate check resolves it to the survivor. |
| BUG-P2-14 | Fixed | A merge writes a history row on every moved deal. |
| BUG-P2-16 | Fixed | Rotation continues after the last served rep even if that rep left. |
| BUG-P2-17 | Fixed | Caps 0..100,000, priorities within the 32-bit range (422 otherwise). |
| BUG-P2-19 | Fixed | The settings row is created with `ON CONFLICT DO NOTHING`. |
| BUG-P2-21, -23 | Fixed | `/pool` takes `limit` (default 1000, max 5000) and `offset`, answers `X-Total-Count`, and clamps `min_minutes`. |
| BUG-P2-24 | Fixed | A comment on a rejected or cancelled request is refused (409). |
| BUG-P2-25 | Fixed | The other leader's notification links to `/transfers`, a page they can read. |
| BUG-P2-26 | Fixed | Clear message when the lead is not routed to any team. |
| BUG-P2-29 | Fixed | Undo is refused (409) on a lost or won deal. |
| BUG-P2-31 | Fixed | A new contact with an invalid phone or email is refused. |
| BUG-P2-32, -34 | Fixed (decision) | Owner decision: an Admin MAY create anyway / override a duplicate block, but a reason is always required; the reason is stored on the account audit row. |
| BUG-P2-33, -35 | Fixed | Negative `limit` and NUL characters answer 4xx (NUL rejected by the schema; `limit` already had `ge=0`). |
| BUG-P2-36 | Fixed | The form shows schema errors next to the field. |
| BUG-P2-38 | Fixed | A rep whose lead waits stays on the form with a confirmation (Pool is leaders and admins only). |
| BUG-P2-39 | Fixed | Re-saving the id of a removed rule or group revives that row. |
| BUG-P2-41 | Fixed | Contacts that came with a locked deal are hidden from people without access. |
| BUG-P2-42 | Fixed | A new group gets its id in the browser, so a rule can use it before the first save. |
| GAP-P2-01 | Fixed | Panel text follows PRD 4.6: "Company already exists, owner: [name]. Contact your Team Leader." |
| GAP-P2-02, -03 | Fixed | Pool filters (source, reason, waiting time, team for Admins) and a live waiting counter. |
| GAP-P2-04 (S4) | Open | `GET /contacts[/{id}]`, a Request-transfer button on the Deal page, `/undo/{audit_id}` path, `rr_pointers.scope_type default` and `category`/`lead_type` rule keys are product scope decisions, not defects to patch quickly. |
| RISK-P2-02 | Partly fixed | Legal forms and Indic names fixed; typos ("Microsfot") and short names remain below 0.5 by design (the threshold is an owner decision). |
| RISK-P2-03 | Fixed | Same change as BUG-P2-27. |
| RISK-P2-04, -05, -06 | Open | 403 vs 404 on writes, bulk spread ignoring caps, and the 25-row `/accounts` cap are unchanged: design decisions for the owner. |

Verification after the fixes: `tests/deep/phase2` 464 passed, 2 skipped, 0 xfail (the two skips need `pg_trgm` and a scratch file); all other backend tests (`tests/` without `deep`) pass; ruff and mypy clean on `app`; web unit tests 54 of 54; browser `e2e/phase2` 45 of 45 pass with no `test.fail` left (API 8102, web 5202, fresh data `qa2fix2`, `CORS_ORIGINS` set). One run was disturbed by the disk filling up (`ENOSPC`, caused by hundreds of leftover embedded-database folders in Temp); the affected tests were re-run and pass. New migration: `0005_qa_phase2.py` (adds `accounts.name_grams` with a GIN index, a partial index for rows without grams, and allows `moved` in `lead_pool_entries.left_reason`).

Test-side changes made with the fixes: the name-pair test compares against `name_similarity`, the transfer-notification test expects `/transfers`, tests that documented the `created_by` widening and the old 409 for a bad contact email now assert the corrected behaviour, merge audit counts are filtered to account rows, the merge-id test uses its own ids (the old test collided with another test's rows and only "failed" by accident), the perf test stores `name_grams` and asserts under 300 ms at 20,000 accounts, and the browser specs follow the new behaviour (confirmation panel, `/transfers` bell link, PRD 4.6 wording).

## Findings

Each BUG has a strict-xfail backend test (or a `test.fail()` browser spec) carrying the id. Remove the mark when the bug is fixed.

### Severity S2

**BUG-P2-15 (BUG, S2): merging into an account without a website fails with 500 about half the time.**
Steps: merge account A (has a website) into account B (no website). Expected (PRD 4.6, Phase 2 6): merge succeeds, B adopts the domain. Actual: `UniqueViolation accounts_domain_uniq` when SQLAlchemy writes B before A (depends on the random UUID order). Test: `test_02_duplicates.py::test_merging_into_an_account_without_a_website_works_whatever_the_ids` (ids fixed so it fails every time). Fix: clear `src.domain` and flush before assigning it to `dst`.

**BUG-P2-18 (BUG, S2): a rule or group can point at another organisation's team.**
Steps: PUT `/settings/assignment` with `target_type=team` and a team id from another org (also `groups[].team_id`). Expected: 404/409. Actual: 200 (the lookup is `session.get(Team, id)` with no org filter); later leads sit in a team of the other org and show its team name; the invariant check flags it. Test: `test_04_engine.py::test_settings_cannot_point_at_another_organisations_team` and the consequence test. Fix: filter by `org_id` like the default-team check.

**BUG-P2-20 (BUG, S2): a direct team move of a lost deal reopens it.**
Steps: lose a deal, Admin `POST /deals/{id}/move-team` with an owner: status becomes `open` with `lost_at` still set (inconsistent); without an owner it becomes `pooled`. Expected: refuse (409) for non-open/pooled deals as transfers do. Test: `test_05_pool_assign.py::test_direct_move_does_not_resurrect_a_lost_deal`. Fix: check status in `move_deal_to_team`.

**BUG-P2-22 (BUG, S2): moving or giving a lead that is waiting in the Pool returns 500.**
Steps: lead waits (cap 0); Admin move-team without an owner, or a give transfer approved. `return_to_pool` adds a second open pool entry (unique index `pool_open_uniq`). Expected: the entry is re-routed. Tests: `test_direct_move_of_a_waiting_lead_without_an_owner`, `test_giving_a_waiting_pool_lead_to_another_team`. Fix: close or update the open entry first.

**BUG-P2-27 (BUG, S2): after an approved transfer the creator in the old team still reads the account page.**
`created_by` widens account scope (also in `/duplicates/check`, where the match is `in_scope`). The old team's creator sees contacts the new team adds later. PRD 4.4 F: the old team loses access at once. Test: `test_06_transfers.py::test_the_old_teams_creator_loses_the_account_after_a_transfer`. Fix: scope by deals only (PRD 4.6), or drop `created_by` once no visible deal remains.

**BUG-P2-37 (BUG, S2, UI): the New lead form always redirects to `/deals/<id>`, even when the lead was given to someone else.**
Most reps will create leads that round-robin gives to a colleague or another team, so the creator lands on a "not found" page. Test: `new-lead.spec.ts` (BUG-P2-37). Fix: go to the deal only if the creator owns it (or can see it), otherwise show the success toast and stay.

**BUG-P2-40 (BUG, S2): a Team Leader cannot give a deal away.**
`GET /teams` returns only the leader's own team and the Give form removes it, so the receiving-team list is empty. Tests: `test_a_team_leader_can_list_the_teams_to_give_a_deal_to`, `transfers.spec.ts` (BUG-P2-40). Fix: an endpoint listing team names (and ids) for leaders.

### Severity S3

- **BUG-P2-01**: whitespace-only company, contact name or next step is accepted (stored as spaces / empty). Test `test_01…::test_whitespace_only_required_fields_are_rejected`. Fix: strip then check length.
- **BUG-P2-05**: website accepts `ftp://`, `mailto:`, spaces in the host, `JavaScript://example.com/%0a…`, IP addresses (PRD: valid URL). The page shows the website as plain text today, so no script runs, but the value is stored. Test `test_website_must_be_a_real_web_address`.
- **BUG-P2-07**: industry and city have no length limit (2 MB values stored). Test `test_industry_and_city_have_a_length_limit`.
- **BUG-P2-08**: two identical creates at once (double click) return 500 for the second (unique domain index). Test `test_creating_the_same_company_twice_at_once…`. Fix: take an advisory lock around check-and-insert, or map the violation to the duplicate response.
- **BUG-P2-09**: two creates with the same phone or email at once both pass the duplicate check and make two companies. Test `test_creating_the_same_phone_twice_at_once…`.
- **BUG-P2-12**: out-of-scope duplicate matches also return the owner's **team name**; PRD 4.6 allows company and owner only. Test `test_out_of_scope_match_shows_only_company_and_owner`. (The QA prompt asked for "no owner"; the PRD wins and allows the owner name, which the code returns.)
- **BUG-P2-13**: after a merge the source's domain is dropped when the survivor has its own, so that website no longer blocks new leads. Test `test_the_merged_accounts_website_still_blocks_new_leads`.
- **BUG-P2-17**: a negative per-rep cap, or a cap or priority above 2^31, returns 500 on `PUT /settings/assignment` (DB check and integer overflow). Test `test_out_of_range_numbers_are_a_clean_422` (4 cases).
- **BUG-P2-21**: the Pool list is cut at 200 rows with no paging or count. Test `test_pool_lists_every_waiting_lead`.
- **BUG-P2-25**: the other Team Leader's "transfer requested" notification links to `/approvals/{id}`, which is hidden from them (404). Tests `test_the_other_leaders_notification_link_opens`, `transfers.spec.ts`.
- **BUG-P2-28**: editing the next step to whitespace is accepted. Test `test_whitespace_only_next_step_is_refused_on_edit`.
- **BUG-P2-30**: creating or editing an account accepts an invalid website that a lead refuses, silently losing the domain. Tests `test_account_website_is_validated_like_on_the_lead_form`, `test_account_patch_junk_website_is_refused`.
- **BUG-P2-31**: a new contact with a valid email and an invalid phone (or reverse) is saved with the bad value silently dropped (PATCH refuses it). Test `test_contact_with_one_good_and_one_bad_channel_is_refused`.
- **BUG-P2-32**: an Admin creates an account without a reason by sending `create_anyway=true` (the reason check receives the text "n/a"). Test `test_admin_account_create_needs_a_reason_even_with_create_anyway`.
- **BUG-P2-33**: `GET /deals?limit=-1` returns 500. Test `test_deals_listing_with_a_negative_limit`.
- **BUG-P2-34**: the reason an Admin must give to create an account is never stored in the audit trail. Test `test_the_admins_reason_for_creating_an_account_is_recorded`.
- **BUG-P2-35**: a NUL character (U+0000) in any free-text field returns 500. Test `test_nul_characters_in_text_are_a_clean_422` (5 fields).
- **BUG-P2-36** (UI): when the server rejects a value with a schema error (company over 200, next step over 500) the form shows nothing at all. Browser: 2 specs. Fix: show `details.errors` next to the field or as a toast.
- **BUG-P2-38** (UI): a rep whose new lead has to wait is redirected to `/pool`, a page refused to reps, and ends on Today. Browser spec.
- **BUG-P2-39**: saving settings with the id of a rule or group removed earlier (stale tab) returns 500 (primary key clash with the soft-deleted row). Test `test_resaving_a_removed_rule_or_group_id_is_not_a_500` (2 cases).
- **BUG-P2-41**: the account page lists contacts that came with another team's locked deal (PRD: no contact outside scope). Tests `test_contacts_of_a_locked_deal_are_not_shown`, `accounts.spec.ts`.
- **BUG-P2-10 (RISK, S3)**: "Pvt Ltd" vs "Private Limited" scores 0.39 and is not flagged. Test `test_pvt_ltd_and_private_limited_are_the_same_company`. Fix: normalise legal-form words before comparing.
- **BUG-P2-11 (RISK, S3)**: Indic scripts are split at vowel signs and viram, so trigram sets are single letters; two different Hindi names score 0.56 and warn. Test `test_different_hindi_names_do_not_warn`. Fix: tokenise with Unicode word rules that keep combining marks, or normalise to a skeleton first.

### Severity S4

- **BUG-P2-02**: `9876543210x12` is accepted and the extension silently dropped.
- **BUG-P2-03**: an email longer than 254 characters is accepted.
- **BUG-P2-04**: `a@b.com.` and `a@b..com` are accepted.
- **BUG-P2-06**: a first-contact date in the future is accepted.
- **BUG-P2-14**: a merge moves deals to another account but writes no history row on the deals themselves.
- **BUG-P2-16**: when the rep served last is deactivated, rotation restarts at the first rep (one rep gets two before another gets one). Test `test_removing_the_last_served_rep_does_not_skip_the_next_in_line`.
- **BUG-P2-19**: the settings row is created lazily, so simultaneous first saves of a new organisation return 500 (one wins).
- **BUG-P2-23**: `GET /pool?min_minutes=1000000000000` returns 500.
- **BUG-P2-24**: a comment is accepted on a transfer whose approval was already rejected or cancelled (status closes lazily).
- **BUG-P2-26**: taking a lead that waits in the Pool with no team says "That deal is already in your team".
- **BUG-P2-29**: Undo still works on a deal that was lost after the edit.
- **BUG-P2-42** (UI): a rule cannot target a group created in the same visit (groups get an id only after a save).

### GAP

- **GAP-P2-01**: PRD 4.6 wants "Company already exists, owner: [name]. Contact your Team Leader." The panel says only "exists in another team" (browser spec, `test.fail`). The QA prompt asked for no owner; PRD wins, so this is reported as a gap.
- **GAP-P2-02**: the Pool page has no filters (source, team, time waiting, reason) although the API has them.
- **GAP-P2-03**: waiting time is a timestamp, not the live counter in the Phase 2 prompt.
- **GAP-P2-04** (S4): no `GET /contacts` or `GET /contacts/{id}` (prompt table lists them); the Deal page has Reassign but no Request transfer button; Undo is `POST /deals/{id}/undo` with a body, not `/undo/{audit_id}`; `rr_pointers.scope_type` has no `default`; `category` and `lead_type` rule keys are accepted but a manual lead never carries them, so such rules never match.

### RISK and NOTE

- **RISK-P2-01 (S2)**: duplicate check and lead creation scale linearly with accounts because the Python trigram scan loads every name (see numbers below). It also runs on the event loop: with 20,000 accounts, 6 simultaneous checks made a trivial `/me` call wait 3.3 s. 300 ms is crossed at roughly 14,000 accounts for one user. Fix: enable `pg_trgm` in production (the migration tries) and make the fallback run in a worker thread.
- **RISK-P2-02 (S3)**: name matching false negatives and positives at 0.5 (full table in the next section).
- **RISK-P2-03 (S3)**: `created_by` makes the creator "in scope" for duplicate matches (account id shown, no deal id) and for the account page, beyond PRD 4.6 (root of BUG-P2-27).
- **RISK-P2-04 (S3)**: write refusals are 403 while reads are 404, so a deal id can be probed for existence by any rep (`pool.assign`, `deal.edit` etc. return 403 for another team's deal, 404 for a random id). Ids are random UUIDs, so exposure is low.
- **RISK-P2-05 (S4)**: bulk "spread" always starts at the lowest-id rep and ignores caps and the round-robin pointer; repeated small bulks load one rep.
- **RISK-P2-06 (S4)**: `/accounts` returns at most 25 rows and has no paging.
- **NOTE**: one lead creation writes three `lead.create` audit rows (account, contact, deal) and a merge two (one per account); the Admin's reason sits only on the deal row. PRD Feature 1 says only a Super admin can override a duplicate block, section 4.4 K and the matrix say Admin too (code follows the matrix). Merge validates "into itself" before permission (409 instead of 403, no denied row). A take request without a named rep is accepted (the deal then waits in the requester's Pool, held), PRD says the leader chooses the rep. The dev server has no favicon (a 404 console line). `CORS_ORIGINS` defaults to port 5173 only. Rep caps and group members are hard-deleted on save (the audit row keeps the old values).

### Name matching at the 0.5 threshold (trigram, measured)

| Existing | New | Score | Warns | Verdict |
| --- | --- | --- | --- | --- |
| Acme Foods Pvt Ltd | Acme Foods Private Limited | 0.39 | no | false negative |
| Microsoft | Microsfot | 0.43 | no | false negative |
| Infosys | Infosis | 0.45 | no | false negative |
| Tata | Tata Motors | 0.42 | no | false negative |
| AB / 3M | AB Corp / 3M India | 0.38 / 0.33 | no | false negative (short names) |
| Zomato | Zomato Media Pvt Ltd | 0.33 | no | false negative |
| Shree Ganesh Traders | Shree Ganesh Textiles | 0.48 | no | borderline |
| Sharma & Sons | Sharma and Sons | 0.73 | yes | good |
| Foods Acme | Acme Foods | 1.00 | yes | good (word order ignored) |
| Reliance Industries | Relaince Industries | 0.67 | yes | good |
| Unit 1 Pvt Ltd | Unit 2 Pvt Ltd | 0.76 | yes | false positive |
| Studio 54 | Studio 55 | 0.67 | yes | false positive |
| Gupta Electricals | Gupta Electronics | 0.50 | yes | borderline |
| two different Hindi names | | 0.56 | yes | false positive (BUG-P2-11) |
| emoji-only names | identical | 0.00 | no | blind (no letters or digits) |

### Duplicate check and create timings (Python trigram scan, one user, median of 5 / 3 runs)

| Accounts | `/duplicates/check` no match | name match | create lead | `/accounts?q=` |
| --- | --- | --- | --- | --- |
| 1,000 | 30 ms | 37 ms | 40 ms | 31 ms |
| 5,000 | 97 ms | 120 ms | 127 ms | 101 ms |
| 20,000 | 529 ms | 590 ms | 453 ms | 459 ms |

Above 300 ms from about 14,000 accounts. Bulk reassign of 400 deals: 2.7 s. Pool, assignment and 100 concurrent creates were all fast and deadlock free.

## What passed (guarantees proved)

- **Data isolation**: 23 id-bearing calls x 4 actors of a second organisation return 403/404/422 for every Phase 2 route, lists and search of the second org contain nothing of the first, a merge across organisations is refused, the same company may exist in two orgs (apart from BUG-P2-18).
- **Authentication**: all 27 Phase 2 routes answer 401 without a token and with a forged one; a deactivated user's token stops working at once.
- **Role matrix**: every read endpoint and 13 write endpoints return the exact expected status per role and scope (User own, Team Leader team, Admin and Super admin all), with denied audit rows written.
- **Duplicate leaks**: outside scope no id, deal, account, contact, stage or value appears in `/duplicates/check`, create errors, headers, `/accounts`, timeline or deal reads, for every match type and viewer (including same-team non-owners and Super admin-owned deals, whose owner name is hidden); except the items above.
- **Engine**: first matching rule by priority wins; Team Leaders, Admins and inactive reps never receive round-robin leads; strict rotation (200 leads over 4 reps differ by at most 1; no rep twice before the others); the pointer is per target; caps are exact under 30 and 100 concurrent creates (repeated 3 times), no deadlock, no 500, no stuck leads after capacity appears; running the engine, the retry job and every outbox event twice changes nothing and sends no second notification; the Team Leader is told once per waiting lead across every retry trigger (settings saved, cap raised, rep created, reactivated, team change, deal lost or deleted).
- **Ownership**: reasons per role, scope for Team Leaders, next steps follow the new owner, previous and new owner are notified, one pool-assign winner among racing callers, bulk reassign writes one audit row per deal plus one for the action, team moves of a rep (move and return to pool) keep every invariant.
- **Transfers**: one open request per deal under 10 simultaneous requests; stale approvals (owner, team, lost, deleted) are refused and leave the deal untouched; approval moves the deal and removes the old owner and old leader's access immediately in the API, lists, search and the live-events feed; only one of two admins deciding at once executes; lookup returns company and team name only, to Team Leaders only.
- **Edit and Undo**: locked fields rejected for every role including Super admin; version conflict; Undo window exact at 0, 9, 9.9 and 10 s allowed and 10.5 s refused; Undo writes its own audit row, the original row and the hash chain stay intact, and tampering with `occurred_at` is detected by the chain check.
- **Integrity**: after 900 fuzz actions (3 seeds) and every scenario, 18 SQL invariants hold (one open pool entry per pooled deal, none otherwise, owners and teams, next steps, audit rows vs pool entries, no duplicate notifications, no merged account with deals), the audit chain verifies, and nothing returned 500 apart from the findings above.
- **Browser**: labels on every input, validation messages next to fields, duplicate panel appears and disappears, blocked state disables Create, Create anyway asks for a reason, Admin override attaches to the existing company, out-of-scope matches show no link or owner or id, keyboard-only form entry, Pool numbers equal the API, live update of the Pool between two browsers (arrival and removal), no sideways scroll at 400 px on all five areas, no console errors or unexpected failed calls on the happy paths.

## Full suite

The full backend suite (existing tests plus my folder) passes with no failures when the other testers' in-progress folders (`tests/deep/phase1`, `tests/deep/phase3`) are left out. A run that included `tests/deep/phase3` showed 38 failures, all inside that folder (another tester's work in progress, not mine), and one run hit a one-off database error in `tests/modules/test_pipeline_engine.py::test_override_rules_by_role` while three testers shared the machine; the file passes alone (13 of 13) and the second full run was clean.

## Open questions for the product owner

1. Out-of-scope duplicate message: show the owner's name and "Contact your Team Leader" as PRD 4.6 says (the QA brief said no owner), and may the team name be shown?
2. Should the creator of a lead keep seeing the company after it moved to another team (current `created_by` rule), given PRD 4.6?
3. Feature 1 says only a Super admin may override a duplicate block; the matrix says Admin too. Which one is right?
4. Should a take request require a named rep (PRD 4.5a wording) or may the receiving leader assign later?
5. Is the 0.5 threshold final, and may legal-form words (Pvt, Private, Ltd, Limited, LLP) be ignored when comparing names? What matters for Indian scripts?
6. How many accounts per organisation are expected in year one (decides when `pg_trgm` becomes mandatory)?
7. Should the Pool show more than 200 waiting leads, and should the creator of a waiting lead see it?
