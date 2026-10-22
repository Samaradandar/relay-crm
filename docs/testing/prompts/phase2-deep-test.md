# Phase 2 deep test prompt: "Lead Pool"

First read `docs/testing/COMMON.md` and follow it exactly. Spec: `Relay_CRM_Phase2_Developer_Prompt.md` and PRD v3.6
(Feature 1, sections 4.4 B, F, 4.6 duplicates, 6, 11). Code: `api/app/modules/leads/`, `accounts/`,
`leads/assignment/`, `leads/transfers.py`.

**Your folders**: `api/tests/deep/phase2/` (add `__init__.py` in `api/tests/deep/` if it does not exist yet, and in yours;
another tester creates the same parent folder at the same time, which is fine), `web/e2e/phase2/`.
Report: `docs/testing/reports/phase2-report.md`. Browser ports: API 8102, web 5202, data folder name `qa2`,
`E2E_REPORT=e2e-report-phase2.json`, `E2E_SCREENS=e2e-results-phase2`.

A lead that is lost, duplicated, mis-assigned or shown to the wrong team costs money. Test accordingly.

## 1. Creating a lead
- Required fields and every validation: company length 1/200/201, whitespace, unicode (Devanagari, Tamil, emoji),
  HTML/SQL strings; contact name; phone in many Indian forms (`9876543210`, `+91 98765 43210`, `098765-43210`, `+919876543210`,
  landline with STD code, 9 and 11 digits, letters, `+1` US number); email edge cases (`.local`, uppercase, `+tag`,
  missing TLD, 255+ chars, IDN); website forms (`example.com`, `www.`, `http://`, `https://x.com/path?q`, port, trailing
  slash, spaces, `javascript:`); lead source not in the list; first contact date in the future; next step empty/
  too long; due date yesterday / today / IST midnight boundary (compare with IST not UTC).
- Phone **or** email required; both given; neither. Extra JSON fields rejected.
- Creation is one transaction: if a later step fails nothing is left (inject a failure, for example by an
  impossible assignment state, and check no account/contact/deal/pool entry remains).
- Exactly one audit row `lead.create` plus the right extra rows for overrides; one `deal.pooled` outbox event.
- Role matrix for creating (User/TL/Admin/SA; Admin needs a reason).

## 2. Duplicate rules (PRD 4.6)
- Exact domain / phone / email matches block (409 `duplicate_blocked`); `www.` and case and trailing slash variants
  count as the same domain; the same phone in different formats counts as the same.
- Similar names warn (409 `duplicate_warning`): test suffix variants (`Pvt Ltd`, `Private Limited`, `& Sons`,
  `and Sons`), typos, word order, one-word names, very short names, names with digits. Measure which pairs trigger
  at the 0.5 threshold and list surprising false positives and negatives in the report (RISK).
- "Create anyway" needs a reason and `duplicate.create_anyway` permission; "override block" needs
  `duplicate.override_or_merge`, attaches to the existing company, writes `duplicate_overrides` rows and audit rows.
- **Leak tests**: a rep in team South creating something that matches a North deal must learn nothing: no ids, no
  owner, no team, no account id, nothing in headers, the same response for in-scope-less matches; test through the
  live `/duplicates/check`, the create call and error bodies. Repeat with every match type and every role.
- Merge: deals and contacts move, source row kept with `merged_into_id`, merging twice, into itself, into a merged
  account (cycle), across orgs, by each role; locked-row display on the account page for out-of-scope deals.
- **Performance of the duplicate check**: the code falls back to a Python trigram scan when `pg_trgm` is absent
  (it is absent on the test database). Load 1,000 / 5,000 / 20,000 accounts and time `/duplicates/check` and lead
  creation; report the numbers and flag anything above 300 ms (RISK with the account count where it breaks).
  If `pg_trgm` can be enabled on the embedded server, compare both paths for identical results.

## 3. Assignment engine
- Rule matching: priority order, inactive rules skipped, match on one/several/no fields, case sensitivity of
  values, rule to team / group / single rep, rule pointing at an empty or fully capped target, default team fallback,
  no default team (waits with reason `no_rule_match`).
- Eligibility: inactive reps, Team Leaders (never by round-robin), reps without a team, a rep in two groups.
- Caps: 0, 1, N, per-rep override, raise/lower while leads wait, "active lead" definition (open deals only; lost/won/
  deleted free the cap), cap check exactly at the boundary under concurrency.
- Fairness: assign 200 leads to 4 reps: counts differ by at most 1; rotation survives adding or removing a rep
  mid-way; the pointer is per target.
- **Concurrency**: 100 leads created at once, repeated several times: each assigned exactly once, caps never exceeded,
  no deadlocks, no 500s, no lead stuck "pooled" when capacity exists after the outbox runs.
- Idempotency: run `assign_pooled` and the outbox handler twice for a lead; never double-assign or double-notify.
- Waiting leads: reason stored, the Team Leader notified **once** per lead, retry triggers (cap raise, new user,
  reactivation, team change, deal closed/deleted, settings saved), the 15-minute job (`pool_retry`), oldest first.
- Notifications: new owner, previous owner, TL of a waiting lead, admins on out-of-scope duplicate, all deduped.
- Settings API: default team required and valid, rules referencing missing targets, caps for unknown users, negative
  caps, huge priorities, duplicated priorities, concurrent saves, reason policy, who may read vs write, the test tool
  is read-only (no audit row, no data change).

## 4. Lead Pool, manual assignment, reassignment
- Pool visibility per role (User denied, TL their team's including routed-but-unassigned, Admin all) and filters;
  the distribution numbers (active leads, cap, assigned in period, methods) cross-checked with raw SQL.
- Manual assign: TL only inside the team, Admin/SA anyone, reason rules, over-cap warning, already assigned (409),
  deleted/lost deal, inactive target, other org target. Reassign and bulk reassign (spread across team, to one rep,
  zero deals, hundreds), next steps move to the new owner, previous owner and new owner notified.
- Direct team move by Admin/SA with and without an owner; `manual_hold` leads are ignored by the engine until assigned.
- Team move of a rep with `open_deals` = `move` and `return_to_pool`.

## 5. Transfers
- Give and take: who may request, deal must be in the requester's team (give), the receiving team, proposed owner,
  one open request per deal (race 10 simultaneous), the other Team Leader's comment (only that TL, Admin, SA),
  decide by Admin (reason), reject, cancel, approving a stale request (deal changed owner/team/status since:
  must refuse with 409), approve moves the deal and **immediately** removes the previous owner's access (read the
  deal as the old owner and old TL), notifications to both Team Leaders and the requester, lookup endpoint returns
  company and team name only and nothing else, lookup for non-TLs refused, rejected/cancelled request does not
  block a new one.

## 6. Edit and Undo
- Editable fields only; locked fields rejected; version conflict (409); required fields never emptied; source change
  after the Lead stage needs `deal.source.change_after_lead`.
- Undo: inside 10 s, at 10 s, at 10.5 s (control the clock by editing `audit_log.occurred_at` through the admin
  connection), by another user, twice, after a later edit of the same field, writes its own audit row and leaves the
  original untouched.

## 7. Companies, contacts, search
- CRUD with each role, scope of `/accounts` search, contact validation (invalid phone/email now rejected), decision
  maker flag, delete behaviour (nothing hard-deleted), merged accounts hidden from search.

## 8. Cross-cutting
- For **every** Phase 2 endpoint: the role matrix, other-org isolation (two organisations), unauthenticated 401,
  extra fields, SQL/HTML injection in text and query params, pagination limits.
- Audit invariants after your whole run: each ok write has exactly one audit row; pooled deals have exactly one open
  pool entry; open deals have an owner and team; no deal has two open pool entries; `lead_pool_entries.left_at` set
  whenever a deal is assigned.
- Model-based fuzz: 300 random actions (create, assign, reassign, transfer, merge, edit, undo, settings change, rep
  deactivate) by random roles with a seeded RNG; check the invariants above and that nothing returned a 500.

## 9. Browser (Playwright, folder `web/e2e/phase2/`)
New lead form (every validation message shown next to its field, live duplicate panel appears/disappears, blocked
state disables Create, similar-name warning shows "Create anyway" and asks for a reason, Admin override flow, out-of-
scope match shows no link or owner, successful create lands on the deal or the Pool); Lead Pool page (waiting reasons
in plain words, assign dialog with caps shown, bulk reassign dialog, distribution tab numbers equal the API);
Transfers page (lookup, take request, give request, comment, approval appears for Admin and executes); Companies list
and account page (locked rows, merge dialog); Settings → Assignment (add rule/group/cap, save with reason, test tool
output); live update (a lead created by one browser context appears on another user's Pool without reload). Phone
width, keyboard-only use of the form, console errors, no horizontal scroll.
