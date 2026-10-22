# Phase 4 File Map (Relay CRM)

Scope: billing (Phase 4) backend modules + the deal-page frontend files touched while adding
inline fit/pain editing, stage-gated fields, and clickable checklist navigation. For anything
outside this list, read the relevant `README.md` in that folder first (every module has one).

Conventions: money = integer paise (bigint). minutes = Decimal(14,3). Every write goes through
`UnitOfWork` (permission check + reason + audit row + outbox event, one transaction). Every
read-scoped list/get goes through `authorize_read`.

## Backend — `api/app/modules/customers/`
Customer record created on `deal.won`; the winning rep keeps the account.

| File | What's in it |
|---|---|
| `models.py` | `Customer` table: account_id (unique), account_owner_id, first_won_deal_id, status, voice_platform_customer_id. |
| `service.py` | `ensure_customer()` — idempotent create-or-fetch, called from `on_deal_won` (outbox handler) and from `billing/draft.py`. `list_customers`/`get_customer` (scoped reads). `set_voice_mapping()` — Admin/SA sets `voice_platform_customer_id`. |
| `router.py` | `GET /customers`, `GET /customers/{id}`, `PATCH /customers/{id}/voice-mapping`. |
| `schemas.py` | `CustomerOut`. |
| `events.py` | `CUSTOMER_CREATED` event name. |

## Backend — `api/app/modules/billing/` (the bulk of Phase 4)

| File | What's in it |
|---|---|
| `models.py` | Every billing table: `BillingSettings` (GST), `Subscription`, `SubscriptionVersion`, `BillingSchedule`, `MinutePack`, `Invoice`, `InvoiceVersion`, `Payment`, `PaymentAllocation`, `MinuteLedger`, `OverageItem`, `BillingAlert`. |
| `service.py` | GST setting only: `read_gst`, `write_gst` (Admin+reason/SA). |
| `draft.py` | Subscription **draft**: `draft_for_deal()` builds a draft subscription from the won deal's catalog lines + Closure data; `draft_on_won` is the outbox handler for `deal.won`. Field parsers: `parse_pricing_model`, `parse_period`, `parse_date`. `snapshot()` is the dict every version/audit row stores. |
| `subscriptions.py` | Subscription **read/confirm/edit**: `confirm_by_id`, `update_contract`, `get_subscription`, `pending_confirmations`, `get_schedule`. On confirm: `_generate_schedule` (SaaS billing_schedule) and `_create_first_pack` (per-minute's first pack). |
| `schedule.py` | Pure date math, no DB: `periods()` splits a contract into billing cycles, `add_months`, `contract_end`, `ist_today`. |
| `invoices.py` | `add_invoice`, `edit_invoice` (direct for Admin/SA, **approval request** for TL via `APPROVAL_TYPE = "billing.invoice_edit"` — deliberately NOT `"invoice.edit"`, which Phase 3's `documents` module already owns as an approval type), `void_invoice`, `list_invoices`/`get_invoice`. `register()` wires the approval executor. |
| `payments.py` | `add_payment` (allocates to invoices, oldest-first default), `reverse_payment` (Admin/SA only, writes a negative correction, never edits the original), `_refresh_invoice_status`/`_advance_paid_through` (derived state), `payment_status()` (Paid/Due/Overdue), `customer_payments_received()` (Phase 6 reads this). |
| `packs.py` | Minute packs + ledger: `draw()` is the core drawdown (oldest-pack-first, splits across packs, overflow becomes `OverageItem`; negative `minutes` = a correction that returns minutes). `add_pack_manually`, `list_packs`. |
| `alerts.py` | `check_balances` (low-balance 20%/run-out), `check_overage`, `check_payment_due_and_overdue`, `check_awaiting_confirmation`, `run_all`. Dedup via `_once()` against `BillingAlert`. |
| `tab.py` | `billing_tab()` — the one aggregator call for the customer billing tab (subscription+versions, packs, invoices, payments, revenue ex-GST/incl-GST). |
| `schemas.py` | All Pydantic in/out shapes for the above (Gst*, Subscription*, Invoice*, Payment*, Pack*). |
| `router.py` | Every `/settings/gst`, `/customers/{id}/subscription|schedule|invoices|payments|usage|packs|billing`, `/subscriptions/...`, `/invoices/...`, `/payments/...` endpoint — thin, calls the service files above. |
| `events.py` | Outbox event name constants. |

## Backend — `api/app/modules/proposals/`
Automated proposal: template → draft → edit → send (PDF + email) → status.

| File | What's in it |
|---|---|
| `models.py` | `ProposalTemplate` (versioned), `Proposal`, `ProposalItem`. |
| `service.py` | `save_template`/`active_template`/`read_template`. `draft_proposal()` freezes catalog lines + deal data into `content` (`_frozen_content`, `_fill_template` for `{{placeholder}}`). `patch_proposal` (rep edits). `send_proposal()` — blocks on pending discount approval, renders PDF, emails it, creates the Phase-3 `documents` record. `set_status`, `versions`. |
| `pdf.py` | `render_pdf()` — WeasyPrint if installed, else `PlainTextFallbackRenderer` (hand-written minimal PDF bytes) so the pipeline runs without native deps. |
| `router.py` / `schemas.py` | `/settings/proposal-template`, `/deals/{id}/proposals`, `/proposals/{id}` (+`/send`, `/status`). |

## Backend — `api/app/modules/usage_feed/`
Hourly pull from the voice platform's usage-api.

| File | What's in it |
|---|---|
| `models.py` | `UsageRecord`, `UnmatchedUsage` (no customer mapping yet), `UsageSyncState` (cursor/health). |
| `service.py` | `run_sync()` — one page = one transaction; `_apply_record()` inserts-once by `source_record_id`, draws the ledger (via `billing/packs.draw`) or writes a `correction`. |
| `client.py` | `fetch_page()` — HTTP client for the external `usage-api` service. |
| `alerts.py` | `alert_sync_failing()` — 3-consecutive-failure alert to Admins/SA. |
| `router.py` | `/usage/unmatched`, `/usage/health`, `/usage/sync` (Admin/SA manual trigger). |

## Backend — other touched files
- `api/alembic/versions/0008_phase4_billing.py` — the one migration for every table above, plus DB triggers: `payment_allocation_guard` (an allocation can never exceed payment/invoice total) and `invoice_total_guard` (can't shrink a total below what's allocated).
- `api/app/platform/jobs/app.py` — added periodic tasks `usage_sync` (hourly) and `billing_alerts_daily` (09:30 IST).
- `api/app/platform/notifications/email.py` — added optional `attachments` param to the email backend (used for proposal PDFs); kept `MemoryBackend.sent` as 3-tuples for backward compatibility, attachments go in a parallel `.attachments` list.
- `api/app/platform/registry.py` — wires `invoices.register()` (approval executor) and the `customer`/`billing` entity scopes.
- `usage-api/app.py` — the separate small service simulating the voice platform's own usage feed.

## Frontend — `web/src/features/deals/` (deal page UX changes)

| File | What's in it |
|---|---|
| `DealPage.tsx` | The deal detail screen. `STAGE_GROUPS` maps each pipeline stage (`lead`, `qualification`, `meeting_scheduled`, `prospecting`, `proposal`, `revision(_2)`, `closure`, `onboarding`) to the `col`/`data` field keys that belong to it — a field only renders while the deal is at its stage (see `show()`/`dataRows` filtering). `goToCriterion()` is the click-to-navigate handler passed into `<Checklist onNavigate=.../>`. Panels got `id="panel-activities|documents|fit|lines|contacts"` wrappers as scroll targets. |
| `Checklist.tsx` | Renders each stage's exit criteria. A **pending** criterion's label is now a button (via `onNavigate`) if `criteriaTargets.ts` has an entry for its key. |
| `criteriaTargets.ts` | `CRITERION_TARGETS` — maps every criterion key (from `api/app/modules/pipeline/seeds/new_business.py`'s `CRITERIA` dict) to where it's completed: a panel id, or `{panel:'edit', focusField}`. `resolveTarget()` special-cases `pricing_selected` (needs either the Lines panel or an Edit-dialog field, picked from which part is actually missing). |
| `FitAndPainPanel.tsx` | New panel, sits next to Documents. Fit checklist (only visible at `stage_key === 'lead'`) and pain point (only at `stage_key === 'qualification'`) — both editable inline, no Edit dialog needed. |
| `EditDealDialog.tsx` | The full edit form (`SECTIONS` list every other field). Added `focusField` prop: on open, scrolls to and focuses `#edit-<focusField>` (ids already existed on each input). Fit checklist and pain point were **removed** from here (moved to `FitAndPainPanel.tsx`). |
| `DocumentsPanel.tsx` / `ActivitiesPanel.tsx` | Unchanged logic; only gained the `id="panel-..."` wrapper in `DealPage.tsx` (not inside these files) as navigation targets. |

## Where the ground truth lives (read this, not the UI, when a criterion's rule is unclear)
`api/app/modules/pipeline/seeds/new_business.py` — `CRITERIA` dict is the single source of truth
for which stage requires which field/activity/document. `criteriaTargets.ts` and `STAGE_GROUPS`
in `DealPage.tsx` were both built by reading this file; if either disagrees with it, trust this
file and fix the frontend maps, not the other way round.
