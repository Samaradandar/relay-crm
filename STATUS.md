# Relay CRM — Build Status

This file tracks what has actually been built and tested, and what's being worked on right now.
For setup/run instructions see `README.md`. For the full spec see `docs/prd/`.

## Done

**Phase 1 — Foundation**
Login (dev auth + Cognito), users and teams, four roles (User/Team Leader/Admin/Super admin)
with Own/Team/All scope, reason rules, audit trail, outbox + job queue, notifications,
approval-request engine, live updates (WebSocket), file uploads.

**Phase 2 — Leads and the Lead Pool**
New-lead creation with duplicate detection, Lead Pool, assignment rules and per-rep caps,
cross-team transfers, a rep's own lead holds for their Team Leader before falling back to
automatic round robin.

**Phase 3 — Pipeline**
Versioned pipeline config, the 9-stage New Business pipeline with proof-checked exit criteria,
deal page, catalog + discount approvals, activity logging, Today view, Kanban board, stage pages,
account history, Lost/Won, profiles, monitoring, exports, delete/restore.

**Phase 4/5 — Billing**
Proposals, agreements, invoices, payments, subscriptions, renewals/cancellations, churn tracking
and churn history.

**Phase 6 — Performance & Reports**
Leaderboard, targets, incentives (original design), personal dashboard, reports, weighted forecast.

**Phase 7 — Automation**
Website/CSV/email lead capture, ML win-probability model infrastructure (training deferred).

**Phase 8 — Google Calendar & Gmail (side track)**
Google OAuth connect/disconnect, one-way and two-way calendar sync with loop prevention, Gmail
thread logging (two-way detection, idempotent), a dev-mode `DevGoogleClient` stand-in so this all
works without a real Google Cloud project.

**Leaderboard & Incentive module (approved 6 Oct 2026, supersedes parts of Phase 6)**
- Backend done: incentive schema v2 (5-value status, pledges), a Team Leader can only
  *request* a rate on a Won deal or *pledge* one on an Active deal — never set it directly;
  Admin/Super admin approve or set directly through the existing Phase 1 approval inbox (no
  second approvals screen). An approved pledge auto-applies with no second approval when its
  deal is Won.
- Not done yet: the Combined Month table, Achievement table, masked User/Team Leader leaderboard
  views, the Admin/SA combined leaderboard, Team Detail and Member Detail pages (L-01–L-07) —
  backend + frontend still to build.

## Doing right now

**Manual QA pass on the running demo**, driven by the user testing the real app in a browser and
reporting what breaks. Found and fixed, each confirmed against the live backend before being
marked done:

1. **Lead Pool decide flow** — a Team Leader could only assign a pooled lead to someone on their
   own team, even when giving it back to its own creator (whose source-routing team could differ
   from their own); fixed. Added a "Round robin now" button and a self-assign option to the
   Pool's assign dialog, wired to the backend's existing `give_creator` / `give_rep` / `auto`
   decide actions (previously only the generic "pick a rep" path was wired to any UI). Shortened
   the default rep-lead hold window from 120 to 20 minutes.
2. **Frontend/backend contract mismatches** — the web client was built against camelCase field
   names that were never actually translated to the backend's snake_case contract, and several
   calls weren't nested the way the backend expects. This silently broke, end to end, confirmed
   by reproducing the exact 422 against the live API for each:
   - Assigning a lead from the Pool (`userId` vs `user_id`)
   - Logging any activity — calls, meetings, emails (`kind`/`at`/`twoWay`/`nextStep`/`followUp`
     vs `type`/`occurred_at`/`two_way`/`next_step_text`/`next_step_due_date`, plus `attendees` as
     a string vs a required list) — this is what broke ticking "Contact verified" and every other
     activity-backed exit criterion
   - Ticking **any** exit criterion (`{activityId}` sent as top-level fields instead of nested
     under `{proof_ref: {activity_id}}`)
   - Moving a deal forward (`to` vs `to_stage`, and the main "Move to next stage" button sent no
     target stage at all), marking a deal Lost (`lostReason` vs `lost_reason`), setting a next
     step (`due`/`lane` values vs `due_date`/`next_step_lane`)

   All of the above are fixed in `web/src/api/index.ts` and `web/src/api/adapters.ts`, and each
   fix was verified by replaying the corrected request body against the live backend before
   being called done — not just "should work now."
3. **Team Leader verify + full pipeline run (15 Oct 2026)** — "Invalid request" (422) when a Team
   Leader clicked Verify: the web sent `decision: verified|rejected`, the API takes
   `verify|reject`. Same class of bug in Override (`to` → `to_stage`), Reassign (`userId` →
   `user_id`) and Assign task (`due` → `due_date`). Deeper: the static checklist in
   `web/src/lib/pipeline.ts` had drifted from the backend (different keys, fit check in the wrong
   stage, field proofs never saved, Lead's two criteria shown "Automatic" but never ticked, ticks
   awaiting check (`pending`) shown as unticked). Live mode now renders the backend's own
   `progress.criteria` (`features/deals/LiveCriteria.tsx`, proof forms in `lib/liveCriteria.ts`),
   saves the proof first (PATCH fields / fit checklist, decision-maker tag, catalog line,
   document) then ticks, and shows the proposal needed to enter Proposal. Team Leader Home
   "Needs you" (assign / verify / proof missing / approve) is composed from existing endpoints.
   API error messages now include the backend's `details`. File upload without S3/MinIO returns
   a clear 503 instead of a 500. Verified end to end in the browser (Playwright): a lead created
   by Sneha Iyer, verified by Tejas Shah, through all 9 stages to Won, zero HTTP errors.
4. **File uploads without Docker** — `run_local.py` now stores files in
   `api/.localdata/<data>-files` (`LocalDiskStorage`, signed `/files/local/` links on the API, never
   used in production), so proposal and agreement files attach without MinIO. Verified in the
   browser: Kerala Spice Route through every stage to Won with real files, no overrides.
5. **Board, stage pages, existing customers** — the Existing customer pipeline was never seeded
   for orgs created by migrations (only `seed_org_pipeline`, used by tests, seeded it), so its
   stage pages and account requests failed with "no published version": migration 0027 backfills
   it. A request now gets the customer's contact and a first next step. The board has an
   Onboarding (and Updated) column for won deals still being set up. Stage pages take their
   columns, exit criteria and cell values from `GET /stages/{key}/deals` (now also returning
   `criteria` and `stage_entered_at`) instead of the static config. Time in stage shows hours
   under a day ("3h in Lead") instead of "0d". "My numbers" moved under Notifications.
9. **Proposal loop, decision maker details, screens for backend-only features (17 Oct 2026)** —
   Prospecting "Decision maker identified" needs the contact's name, phone and email. Proposal is a
   loop (`api/app/modules/proposals/rounds.py`, migration 0029): a held walkthrough meeting, then
   the customer's answer; changes or objections close the round, clear its ticks and need a NEW
   meeting to revise the proposal; once accepted the proposal is drafted from the template and
   the deal's products, edited, and emailed (recipient name and email) and "Decision date set"
   follows. A rejection goes straight to Mark lost. Nothing is needed to enter Proposal any more.
   New criterion `proposal_emailed` (not `proposal_sent`: Existing customers already uses that key).
   Rolled out with `python scripts/publish_pipeline_rules.py --all` (version 3 locally). Screens
   added for backend features that had none: Proposal template (Settings), Settings lists (fit
   checklist, lost reasons; the Lost dialog and page now use the live list), Import leads (CSV
   wizard), Capture review and Lead capture (website form sources), Transfers, and working
   Teams buttons (create, rename, new colour, move a rep, change Team Leader) plus a password
   reset button. Fixed on the way: creating a user dropped their team, and deactivating or
   moving a user from the Users page was ignored (the web sent `teamId`/`active`, the API reads
   `team_id`/`is_active`). Still without a screen: Google Calendar/Gmail connect, calendar
   suggestions and Gmail threads; invoice edit/void and payment reversal; usage-feed admin;
   custom fields; bulk targets; the admin stats recompute. NOT RUN: no tests were run for this
   change (static checks only); the shared test helpers and some tests were updated to the new
   flow but have not been executed, and there are no tests yet for rounds or the new screens.
   Follow-up: choosing Changes requested or Objections now also asks for the next meeting's date and time
   (`next_meeting_at`, must be in the future); the round closes and that meeting is booked in the same
   save, any older booking is cancelled, and the rep marks it held when it happens. Not run.
   (Superseded by the 20 Oct redesign below: the loop now stays in Proposal.) Changes or objections sent it back to Meeting scheduled (`send_back` in
   `pipeline/engine/service.py`), then Prospecting, then Proposal again until accepted; Meeting held now
   has to be a meeting after the last closed round (pipeline version 6). On acceptance the draft opens with
   the conditions the rep writes, then the Prospecting answers (use case, pain point, budget, pricing),
   then the template text. Not run.
   Documents the company receives (migration 0030, kinds advance / prerequisite / technical, status
   received): Closure now also needs "Advance received" (receipt file, amount, who received it, their
   contact, date) and Onboarding starts with "Pre-requisite document received" and "Technical document
   received" (file, what it covers, date), before kickoff, live and first invoice. Pipeline version 7.
   Deals already in Closure or Onboarding are brought up to date with `python scripts/refresh_stage_checklists.py
   --stage closure --stage onboarding` (keeps their ticks; done locally for 9 deals).
   Test helpers updated, not run.
   Proposal redesign (20 Oct): the loop stays inside Proposal (no trip back to Meeting scheduled). Accepted opens
   a form of the accepted terms (product lines with description and price per unit, business model, billing
   frequency per model, minimum committed minutes, concurrency, tax %, scope of work, terms and conditions, company
   name and details) and the draft is built from them (`POST /deals/{id}/proposals` with `DraftIn`,
   `GET /deals/{id}/proposal-prefill`); a price under the approved one is refused. Revision shows the same terms
   form to draft a revised version. Not run.
   The walkthrough is a NEW meeting: only meetings logged after the deal entered Proposal count for a round
   (`created_at >= stage_entered_at`, in `rounds.py` and the `walkthrough_logged` proof); the Meetings panel in
   Proposal lists only this stage's meetings. Not run.
10. **Waiting-on-them check, paging, stage history, incentives (21 Oct 2026)** — "Waiting on them" was checked end to
   end (set the lane, Today shows it under its own heading with Nudge / They replied, snooze, replied): works.
   Paging: `GET /deals` served only the first 100 rows (cap 500, no offset), so any list built from it silently
   lost deals past that; it now takes `offset` and the web reads every page (also approvals and the audit log,
   which stopped at 500). Stage history: `GET /deals/{id}/stage-history` and a clickable stage bar on the deal
   page: any earlier stage opens read-only with its checklist, who ticked what and when, the values and the
   meeting behind each tick. Incentives: new Incentives tab (`/app/incentives`) for every role; when the last
   Onboarding criterion is ticked a `deal.onboarding_completed` event tells the Team Leader to set the incentive
   (and the rep that it is coming); the Team Leader sends a % for approval (`POST /deals/{id}/incentive/request`),
   an Admin or Super admin approves or rejects it on the same page, the rep is told when it is approved, and it is
   provided as the customer pays (earned once paid in full: the existing rule). Not run through a full
   onboarding-to-approval cycle; no tests added.
8. **Meeting lifecycle (17 Oct 2026)** — a meeting is now booked, held, postponed, cancelled or
   no-show (migration 0028; `api/app/modules/activities/meetings.py`). Qualification passes with a
   meeting booked *or* held (with attendees); Meeting Scheduled passes only once a meeting is
   *held* (attendees and what was discussed). Postponing keeps the old booking, marked postponed
   with the reason, and links the new one; cancelling / no-show records the reason, and a tick that
   relied on that meeting shows Proof missing until a new one is booked. The logger has two
   choices (Meeting held / Book a meeting) and the deal page has a Meetings panel (Meeting held,
   Postpone, Cancel). Existing orgs get the rule as a new pipeline version with
   `python scripts/publish_pipeline_rules.py --all` (done locally; it also carries the decision-maker and proposal rules); deals already in a
   stage keep the version they entered under. Not yet covered by tests: the new endpoints, and
   some older tests were updated but not re-run to completion.
7. **Contact type on a lead (16 Oct 2026)** — the Add new lead form has a "Contact type" field
   (Influencer, the default, or Decision maker) and no longer asks for a due date (the first next
   step is due tomorrow IST automatically). The type is stored on the contact
   (`is_decision_maker`), shown on the deal page, board cards and stage pages, and can be changed
   at any stage with one click on the deal page (owner, Team Leader, Admin; an Admin gives a
   reason). Each change is in the audit log. Marking the only decision maker as an influencer
   makes "Decision maker identified" show Proof missing until another contact is tagged.
6. **Sync check of every page** — every web API call compared with the OpenAPI routes, every
   route crawled as rep / Team Leader / Admin / Super admin, page figures compared with the API.
   Fixed: Audit log (called a missing `/audit`: new `GET /audit`, Admin/SA only, with record
   names), Pipeline settings (called missing `/settings/pipelines/*`: now reads and publishes the
   real `/pipelines/{key}/versions`; adding a new criterion is not offered in live mode), Export CSV
   (`POST /exports/log` did not exist: now `POST /exports`), Deleted records (showed the lost date,
   no reason), Pool "assigned today" (hard-coded 0), "Set incentive rates" hidden for Team Leaders
   (the API only lets Admin/SA set rates; the Team Leader request/pledge screen is still to build),
   My Today no longer asks non-reps for `/me/numbers`. After the fixes: no HTTP or page errors on
   any route for any role, and the checked figures match the API.

## Next up

- Continue the manual QA pass as more pages get clicked through.
- Build the remaining Leaderboard & Incentive frontend (L-01–L-07).
- Still open: exact Last-Quarter/Last-Year window definition, Target-Attainment colour threshold
  for the 70–89% gap, and whether HR's leaderboard-visibility sign-off needs revisiting given the
  masking-rule change — none of these have been answered yet.
