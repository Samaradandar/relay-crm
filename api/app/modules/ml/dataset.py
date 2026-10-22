# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P7-04: training-data builder. One row per (closed new-business deal, stage entry up to and
excluding Closure/Onboarding -- Onboarding is Won by definition and excluded). Label: 1 = the
deal eventually Won, 0 = Lost.

Leakage safety: every feature is either (a) immutable once the stage entry happens (the entry's
own stage/round, activity/criteria counts bounded by `occurred_at`/`ticked_at` <= the entry's
`entered_at`, which are append-only event logs so this is safe by construction), or (b)
reconstructed from `audit_log` as its value at `entered_at` via `_value_as_of()` for the handful
of deal fields a rep can edit later (value_paise, pricing_model, plan, expected_minutes_per_month,
expected_close_date). `_value_as_of()` is also what test_leakage.py checks against a deal whose
value changed after the snapshot.

No rep/team/person field is ever a feature (PRD: protects against the model learning "who" not
"what"); `generated_by_kind` captures only rep vs. website/csv/email, never which rep.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.models import Contact
from app.modules.activities.models import Activity
from app.modules.leads.models import Deal
from app.modules.pipeline.engine.models import DealCriterion, DealStageEntry
from app.platform.audit.models import AuditLog

MIN_CLOSED_DEALS = 300
MIN_PER_CLASS = 60

#: fields a rep can edit after the deal is created -- reconstructed from audit history, never
#: read live off the Deal row, to avoid leaking a value set after the snapshot time.
EDITABLE_FIELDS = (
    "value_paise",
    "pricing_model",
    "plan",
    "expected_minutes_per_month",
    "expected_close_date",
)
EDIT_KEYS = (
    "deal.edit", "deal.value.up_to_5", "deal.value.5_to_10", "deal.value.above_10",
    "deal.close_date.change", "deal.pricing.change_after_prospecting",
)  # fmt: skip

FEATURE_LIST = [
    "stage_key", "revision_round", "days_in_previous_stage",
    "lead_source", "industry", "city", "generated_by_kind", "assignment_method",
    "activities_7d", "activities_30d", "two_way_activities", "days_since_last_activity",
    "meetings_held",
    "criteria_ticked", "criteria_verified", "criteria_rejected", "criteria_overridden",
    "proposal_sent", "value_paise", "pricing_model", "plan", "expected_minutes_per_month",
    "days_to_expected_close", "contact_count", "decision_maker_tagged",
]  # fmt: skip


async def _value_as_of(
    session: AsyncSession,
    org_id: uuid.UUID,
    deal_id: uuid.UUID,
    field: str,
    at: datetime,
    current: Any,
) -> Any:
    """`field`'s value at time `at`: the latest audit row touching it before `at`, else the
    deal's current value (safe only because nothing has ever changed it, which this function
    itself verifies by finding no such row)."""
    rows = (
        await session.execute(
            select(AuditLog.new_value, AuditLog.occurred_at)
            .where(
                AuditLog.org_id == org_id,
                AuditLog.entity_type == "deal",
                AuditLog.entity_id == str(deal_id),
                AuditLog.action_key.in_(EDIT_KEYS),
                AuditLog.occurred_at <= at,
            )
            .order_by(AuditLog.occurred_at.desc())
        )
    ).all()
    for new_value, _at in rows:
        if new_value and field in new_value:
            return new_value[field]
    # no edit touched this field before `at` -- but if one touched it AFTER `at`, the current
    # value is NOT safe to use; check for that and fall back to None rather than leak.
    after = (
        await session.execute(
            select(AuditLog.id).where(
                AuditLog.org_id == org_id, AuditLog.entity_type == "deal",
                AuditLog.entity_id == str(deal_id), AuditLog.action_key.in_(EDIT_KEYS),
                AuditLog.occurred_at > at,
            ).limit(1)
        )
    ).first()  # fmt: skip
    return None if after else current


async def _rows_for_deal(session: AsyncSession, deal: Deal, label: int) -> list[dict[str, Any]]:
    from app.modules.accounts.models import Account

    account = await session.get(Account, deal.account_id)
    entries = (
        await session.execute(
            select(DealStageEntry)
            .where(DealStageEntry.deal_id == deal.id)
            .order_by(DealStageEntry.entered_at)
        )
    ).scalars().all()  # fmt: skip
    out: list[dict[str, Any]] = []
    prev_exit: datetime | None = None
    for entry in entries:
        if entry.stage_key in ("onboarding", "closure"):
            continue  # "only stages up to Closure" -- Closure and Onboarding excluded
        at = entry.entered_at
        days_in_prev = (at - prev_exit).days if prev_exit else None
        prev_exit = entry.exited_at or at

        acts = (
            await session.execute(
                select(Activity.occurred_at, Activity.two_way, Activity.type).where(
                    Activity.deal_id == deal.id, Activity.occurred_at <= at
                )
            )
        ).all()
        day_secs = 86400
        activities_7d = sum(1 for o, *_ in acts if (at - o).total_seconds() <= 7 * day_secs)
        activities_30d = sum(1 for o, *_ in acts if (at - o).total_seconds() <= 30 * day_secs)
        two_way = sum(1 for _, tw, _ in acts if tw)
        meetings = sum(1 for _, _, t in acts if t == "meeting")
        last_activity = max((o for o, *_ in acts), default=None)
        days_since_last = (at - last_activity).days if last_activity else None

        crit = (
            await session.execute(
                select(DealCriterion.verify_status).where(
                    DealCriterion.deal_id == deal.id, DealCriterion.ticked_at <= at
                )
            )
        ).scalars().all()  # fmt: skip
        ticked = len(crit)
        verified = sum(1 for v in crit if v == "verified")
        rejected = sum(1 for v in crit if v == "rejected")
        overridden = sum(1 for v in crit if v == "overridden")

        contacts = (
            await session.execute(
                select(Contact.is_decision_maker).where(
                    Contact.account_id == deal.account_id, Contact.created_at <= at
                )
            )
        ).scalars().all()  # fmt: skip

        value_paise = await _value_as_of(
            session, deal.org_id, deal.id, "value_paise", at, deal.value_paise
        )
        pricing_model = await _value_as_of(
            session, deal.org_id, deal.id, "pricing_model", at, deal.pricing_model
        )
        plan = await _value_as_of(session, deal.org_id, deal.id, "plan", at, deal.plan)
        minutes = await _value_as_of(
            session,
            deal.org_id,
            deal.id,
            "expected_minutes_per_month",
            at,
            deal.expected_minutes_per_month,
        )
        close_date = await _value_as_of(
            session, deal.org_id, deal.id, "expected_close_date", at, deal.expected_close_date
        )
        if isinstance(close_date, str):
            close_date = date.fromisoformat(close_date)
        days_to_close = (close_date - at.date()).days if close_date else None

        out.append(
            {
                "deal_id": str(deal.id),
                "label": label,
                "snapshot_at": at.isoformat(),
                "stage_key": entry.stage_key,
                "revision_round": entry.revision_round,
                "days_in_previous_stage": days_in_prev,
                "lead_source": deal.lead_source,
                "industry": account.industry if account else None,
                "city": account.city if account else None,
                "generated_by_kind": deal.source_kind or "rep",
                "assignment_method": deal.assignment_method,
                "activities_7d": activities_7d,
                "activities_30d": activities_30d,
                "two_way_activities": two_way,
                "days_since_last_activity": days_since_last,
                "meetings_held": meetings,
                "criteria_ticked": ticked,
                "criteria_verified": verified,
                "criteria_rejected": rejected,
                "criteria_overridden": overridden,
                "proposal_sent": entry.stage_key in ("revision", "revision_2", "closure"),
                "value_paise": value_paise,
                "pricing_model": pricing_model,
                "plan": plan,
                "expected_minutes_per_month": minutes,
                "days_to_expected_close": days_to_close,
                "contact_count": len(contacts),
                "decision_maker_tagged": any(contacts),
            }
        )
    return out


async def closed_deal_progress(session: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    """For the "214 of 300 closed deals" progress message."""
    rows = (
        await session.execute(
            select(Deal.status).where(
                Deal.org_id == org_id,
                Deal.pipeline == "new_business",
                Deal.status.in_(["won", "lost"]),
                Deal.deleted_at.is_(None),
            )
        )
    ).scalars().all()  # fmt: skip
    won = sum(1 for s in rows if s == "won")
    lost = len(rows) - won
    return {
        "total": len(rows),
        "won": won,
        "lost": lost,
        "ready": len(rows) >= MIN_CLOSED_DEALS and won >= MIN_PER_CLASS and lost >= MIN_PER_CLASS,
    }


async def build_dataset(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """Section P7-04. S3 upload of the Parquet file and the `ml_datasets` row are left to the
    caller (router/service), which also needs an Admin/SA actor for the audit row -- this
    function is the pure data-shaping half, and is what the leakage test exercises directly."""
    deals = (
        (
            await session.execute(
                select(Deal).where(
                    Deal.org_id == org_id,
                    Deal.pipeline
                    == "new_business",  # existing-customer deals: a separate model, if ever
                    Deal.status.in_(["won", "lost"]),
                    Deal.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    rows: list[dict[str, Any]] = []
    won = lost = 0
    for deal in deals:
        label = 1 if deal.status == "won" else 0
        won += label
        lost += 1 - label
        rows.extend(await _rows_for_deal(session, deal, label))
    return {
        "rows": rows,
        "row_count": len(rows),
        "won_deals": won,
        "lost_deals": lost,
        "feature_list": FEATURE_LIST,
    }
