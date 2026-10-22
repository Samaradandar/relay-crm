# Phase 4 (billing) deep adversarial test report — 2026-10-09

## Scope
Deep, adversarial functional testing of `api/app/modules/billing/*` beyond the existing
`api/tests/billing/test_*.py` files. New tests added under `api/tests/billing/deep/test_adversarial.py`
(12 tests). Areas probed per the task brief: GST rate handling, customer-on-won idempotency,
subscription draft/confirm, invoice numbering, payments/allocation, minute packs/usage, alerts,
cross-org isolation (IDOR spot checks).

No PRD/phase-prompt files were found in `docs/` under billing/GST/invoice/subscription/pack/usage
search, nor in the scratchpad `txt/` extract directory referenced in the task — only
`docs/testing/COMMON.md` and the billing module's own docstrings were available as spec. Findings
below are judged against the module docstrings (which read as the authoritative intent for Phase 4)
and the hard invariants stated in COMMON.md (org isolation, integer paise, append-only audit).

## Test counts
- New file: `api/tests/billing/deep/test_adversarial.py` — 12 tests, all passing after fixes.
- Full `api/tests/billing` suite (existing 10 files + new deep file): **68 passed**.
- `api/tests/deep/phase2` and `api/tests/deep/phase3` (pipeline/lead invariants, to confirm the
  Won hook wiring wasn't disturbed): run in background; see note below if not yet landed.

## Bugs found and fixed

### BUG-1 (S1/S2 — data integrity): a payment could be allocated to a voided invoice
- **File**: `api/app/modules/billing/payments.py`, `add_payment`
- **Repro**: create an invoice, void it, then `POST /customers/{id}/payments` with an explicit
  `allocations` list naming that invoice. The request succeeded (201) and inserted a
  `payment_allocations` row against the void invoice; the payment came back with
  `unallocated_paise: 0` (fully consumed), even though a voided invoice must never receive money.
  The default (no explicit `allocations`) path was already safe — `_open_invoices` filters to
  `status IN ('open','part_paid')` — but the explicit-allocation path skipped that check entirely,
  and the DB trigger (`payment_allocation_guard`) only guards the payment/invoice *totals*, not
  invoice status.
- **Fix**: `add_payment` now raises `ValidationFailed` (422) if any explicitly-named invoice has
  `status == "void"`, before any row is inserted.
- **Test**: `test_payment_cannot_be_recorded_against_a_voided_invoice_via_explicit_allocation`
  (fails against the old code with a 201, passes against the fix with a 422).

### BUG-2 (S1 — concurrency/data integrity): concurrent usage draws could overdraw a minute pack
- **File**: `api/app/modules/billing/packs.py`, `_draw_packs` / `draw`
- **Repro**: a 100-minute pack, two concurrent calls to `draw(..., Decimal("60"))` for the same
  customer (simulating two usage-sync workers processing different usage records at the same
  moment). Both transactions ran under Postgres's default READ COMMITTED isolation, both summed
  the ledger and saw the same pre-draw balance (100), both decided there was room, and both drew
  the full 60 minutes. Result: the pack ended up with 120 minutes drawn against a 100-minute
  pack — a genuine lost-update race, and no overage item was recorded for the overdraft, so the
  invariant in the module's own docstring ("A balance is always `size_minutes - sum(ledger rows)`")
  silently broke (went negative) with no trace in `overage_items`.
- **Fix**: `_draw_packs` now locks the customer's pack rows with `SELECT ... FOR UPDATE` before
  computing balances, so the second concurrent transaction blocks until the first commits its
  ledger inserts, then reads the fresh (already-drawn-down) balance.
- **Test**: `test_concurrent_draws_never_overdraw_a_pack` (failed with ledger sum 120 > 100
  against the old code; passes, and asserts `drawn + overage == 120`, against the fix).

## What passed (guarantees confirmed solid)
- Cross-org isolation spot checks on `subscriptions.update_contract` and `payments.add_payment`:
  a forged Actor from a different org, given a leaked/guessed `customer_id` from the first org,
  is correctly rejected (`NotFound`/`OutOfScope`/`PermissionDenied`) by the existing
  `uow.authorize(... entity=("customer", customer_id))` + scope-check pattern. No missing
  `org_id` filter was found in the 2 mutation endpoints spot-checked this way.
- GST rate is correctly bounded to `[0, 10000]` basis points at the schema layer
  (`GstPut.gst_rate_bp: Field(ge=0, le=10000)`); boundary values 0, 10000 accepted, -1 and 10001
  rejected with 422.
- A GST rate change does not retroactively alter already-recorded invoices — invoices store their
  own `gst_paise` at creation time, independent of the org's current `billing_settings` row.
- Invoice numbers are unique per org at the database level (`Conflict`/409); a concurrent
  double-POST of the same number correctly yields exactly one 201 and one 409, and exactly one
  row in `invoices` (verified by direct SQL, not just the HTTP responses).
- Explicit duplicate-invoice allocation that together exceeds the invoice total (same invoice id
  twice in one payment's `allocations`, each within total alone) is correctly rejected as one
  atomic failure by the `payment_allocation_guard` DB trigger — zero allocation rows persist.
- Subscription confirm is correctly idempotent-safe: confirming an already-confirmed subscription,
  or confirming with a required field missing (e.g. `fee_paise` for a SaaS subscription), is
  rejected (422) without partially applying any field — `_check_shape` runs and raises before the
  status flips to `confirmed`, and no half-written row was observed.
- A `User`-role actor cannot confirm a subscription (403/404, scope-dependent) — consistent with
  the rest of the app's permission matrix.
- (From the pre-existing test files, re-confirmed by inspection, not re-tested): replaying the
  `DEAL_WON` event twice creates no duplicate customer (unique index on `(org_id, source_deal_id)`
  / `ensure_customer`'s idempotent upsert).

## Could not fully test / open questions
- GST **rounding on odd amounts** (task item 1) does not apply as a separate risk: the product
  code never computes GST from a rate — `amount_ex_gst_paise` and `gst_paise` are both supplied
  directly by the caller on `POST /customers/{id}/invoices`, with only a DB `CHECK` that
  `total_paise = amount_ex_gst_paise + gst_paise`. There is no rate x amount multiplication in
  this phase to probe for rounding error. Flagging as a **GAP/NOTE** rather than a bug: if a future
  phase adds client-side or server-side auto-computation of GST from the rate, rounding-mode
  (round-half-up vs banker's) should be decided and tested then.
- Payment **reversal, refunds, overpayment/underpayment, usage pack expiry, and alert
  dedupe/threshold-boundary** behaviors were read in detail (`payments.reverse_payment`,
  `packs.draw`'s negative-correction branch, `alerts.check_balances`'s `pct <= 20` boundary) and
  look internally consistent with their docstrings, but were not independently re-tested beyond
  what `test_payments.py`, `test_packs_and_usage.py` and `test_alerts.py` already cover, given the
  time budget for this pass. These are reasonable next targets for a further round.
- No PRD or phase-prompt document could be located to check these assumptions against written
  spec; recommend confirming with the product owner whether GST auto-calculation (and its
  rounding rule) is in-scope for a later phase.

## Web UI gap (explicit note, as requested)
There is **no browser UI for billing at all** — confirmed via `web/src/features/*` (no
`billing`/`invoice`/`subscription`/`payment`/`pack` folder exists; only `customers`, `accounts`,
`deals`, etc.). Everything a Team Leader or Admin can do through the backend today requires a
direct API call; none of it is reachable by clicking through the app. Concretely, through the
browser today, a Team Leader/Admin **cannot**:
- See or confirm a subscription draft that was auto-created when a deal was won (`GET
  /subscriptions/pending`, `POST /subscriptions/{id}/confirm`) — there is no "awaiting
  confirmation" queue in the UI, only the `billing.awaiting_confirmation` alert's backend
  notification (if notifications are surfaced elsewhere in the UI; the billing tab itself does
  not exist to click into).
- View a customer's billing tab (`GET /customers/{id}/billing`) — the consolidated
  subscription/invoices/payments/usage/revenue view this endpoint already assembles has no page.
- Record or view invoices, record or reverse payments, or view/manage minute packs and overage for
  any customer.
- Change the org's GST rate (`PUT /settings/gst`).
- See or act on any billing alert (low balance, run-out, overage, payment due/overdue) as a
  specific actionable item tied to a customer — alerts only exist as backend `billing_alerts`
  rows plus a notification, with no billing page to land on.

This means Phase 4 is currently **API-only**; every guarantee verified above (org isolation, void
protection, concurrency safety, idempotent confirm) is real at the service layer, but none of it is
yet usable by a human through the product.
