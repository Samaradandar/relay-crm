# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Editing a deal and the 10-second Undo (PRD 11 Feature 3, P3-05).

Which permission an edit needs depends on the field and on the stage:
  lead source after the Lead stage          deal.source.change_after_lead
  expected close date once it is set        deal.close_date.change
  pricing model, plan, volume after Prospecting   deal.pricing.change_after_prospecting
  everything else                           deal.edit
The strictest reason rule of the keys involved applies. Required fields can be replaced but never
emptied. Undo is a compensating edit with its own audit row; the original row is never touched.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.core.time import utc_now
from app.modules.deals import health
from app.modules.deals.common import load_deal
from app.modules.deals.steps import check_step, open_step
from app.modules.leads import events
from app.modules.leads.models import Deal, NextStep
from app.modules.leads.ownership import deal_rt
from app.modules.pipeline.config.models import CustomFieldDef, FitItem
from app.modules.pipeline.engine.service import published_version, recheck_proofs, stages_of
from app.platform.audit.models import AuditLog
from app.platform.context import Actor, RequestInfo
from app.platform.uow import UnitOfWork

UNDO_WINDOW_SECONDS = 10

COLUMN_FIELDS = (
    "lead_source",
    "first_contact_date",
    "expected_close_date",
    "expected_close_reason",
    "pricing_model",
    "plan",
    "expected_minutes_per_month",
    "use_case",
    "pain_point",
)
DATA_FIELDS = (
    "budget_amount_paise",
    "budget_source",
    "feedback",
    "decision_date",
    "final_price_paise",
    "scope",
    "agreed_by",
    "billing_contact",
    "subscription_start_date",
    "contract_term",
    "billing_frequency",
    "renewal_date",
    "go_live_date",
    "live_date",
    "live_note",
    "go_live_delayed",
    "prereq_documents",
    "prereq_received_on",
    "technical_documents",
    "technical_received_on",
    "advance_amount_paise",
    "advance_received_by",
    "advance_received_contact",
    "advance_received_on",
)
STEP_FIELDS = ("next_step_text", "next_step_due_date")
PRICING_FIELDS = ("pricing_model", "plan", "expected_minutes_per_month")
NEVER_EMPTY = ("lead_source", "next_step_text", "next_step_due_date")
EDIT_KEYS = (
    "deal.edit",
    "deal.source.change_after_lead",
    "deal.close_date.change",
    "deal.pricing.change_after_prospecting",
)
ALL_FIELDS = (*COLUMN_FIELDS, *DATA_FIELDS, *STEP_FIELDS, "fit_checklist", "custom_fields")


def _jsonable(v: Any) -> Any:
    return v.isoformat() if isinstance(v, date) else v


def snapshot(deal: Deal, step: NextStep | None) -> dict[str, Any]:
    out: dict[str, Any] = {f: _jsonable(getattr(deal, f)) for f in COLUMN_FIELDS}
    out.update({f: (deal.data or {}).get(f) for f in DATA_FIELDS})
    out["next_step_text"] = step.text if step else None
    out["next_step_due_date"] = _jsonable(step.due_date) if step else None
    out["fit_checklist"] = dict(deal.fit_checklist or {})
    out["custom_fields"] = dict(deal.custom_fields or {})
    return out


def _set(deal: Deal, step: NextStep | None, name: str, value: Any) -> None:
    if name in COLUMN_FIELDS:
        if name in ("first_contact_date", "expected_close_date") and isinstance(value, str):
            value = date.fromisoformat(value)
        setattr(deal, name, value)
    elif name in DATA_FIELDS:
        data = dict(deal.data or {})
        if value is None:
            data.pop(name, None)
        else:
            data[name] = _jsonable(value)
        deal.data = data
    elif name == "next_step_text" and step is not None:
        step.text = value
    elif name == "next_step_due_date" and step is not None:
        step.due_date = date.fromisoformat(value) if isinstance(value, str) else value
    elif name == "fit_checklist":
        deal.fit_checklist = value
    elif name == "custom_fields":
        deal.custom_fields = value


async def _clean_custom(session: AsyncSession, deal: Deal, given: dict[str, Any]) -> dict[str, Any]:
    defs = {
        d.key: d
        for d in (
            await session.execute(
                select(CustomFieldDef).where(
                    CustomFieldDef.org_id == deal.org_id,
                    CustomFieldDef.entity == "deal",
                    CustomFieldDef.active.is_(True),
                )
            )
        ).scalars()
    }
    merged = dict(deal.custom_fields or {})
    for key, value in given.items():
        d = defs.get(key)
        if d is None:
            raise ValidationFailed(f"Unknown custom field '{key}'", {"field": key})
        if value is None or value == "":
            merged.pop(key, None)
            continue
        ok = {
            "text": isinstance(value, str),
            "number": isinstance(value, int | float) and not isinstance(value, bool),
            "yes_no": isinstance(value, bool),
            "date": isinstance(value, str) and _is_date(value),
            "dropdown": value in d.options,
            "file": isinstance(value, str),
            "contact": isinstance(value, str),
        }[d.type]
        if not ok:
            raise ValidationFailed(f"'{d.label}' has the wrong kind of value", {"field": key})
        merged[key] = value
    return merged


def _is_date(v: str) -> bool:
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


async def _clean_fit(session: AsyncSession, deal: Deal, given: dict[str, bool]) -> dict[str, Any]:
    ids = {
        str(i.id)
        for i in (
            await session.execute(
                select(FitItem).where(FitItem.org_id == deal.org_id, FitItem.active.is_(True))
            )
        ).scalars()
    }
    merged = dict(deal.fit_checklist or {})
    for key, ticked in given.items():
        if key not in ids:
            raise ValidationFailed("That fit-check item does not exist", {"field": "fit_checklist"})
        merged[key] = bool(ticked)
    return merged


async def patch_deal(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, patch: Any
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Returns (deal id, audit id for Undo or None when nothing changed)."""
    sent = set(patch.model_fields_set) - {"expected_version", "reason"}
    if not sent:
        raise ValidationFailed("Nothing to change")
    for name in NEVER_EMPTY:
        if name in sent and getattr(patch, name) is None:
            raise ValidationFailed("This field cannot be emptied", {"field": name})
    if patch.next_step_due_date is not None:
        check_step(patch.next_step_text or "x", patch.next_step_due_date)

    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        entity = ("deal", deal.id)
        await uow.authorize("deal.edit", entity=entity, owner_id=deal.owner_id)
        if deal.status == "lost":
            raise Conflict("Reopen the deal before editing it")
        step = await open_step(s, deal.id, lock=True)
        if ("next_step_text" in sent or "next_step_due_date" in sent) and step is None:
            raise Conflict("This deal has no open next step to edit")
        stages = await stages_of(s, (await published_version(s, deal.org_id, deal.pipeline)).id)
        here = stages.get(deal.stage_key)
        entry = next((st for st in stages.values() if st.is_entry), None)
        prospecting = stages.get("prospecting")
        before = snapshot(deal, step)

        values: dict[str, Any] = {}
        for name in sent:
            value = getattr(patch, name)
            if name == "custom_fields":
                value = await _clean_custom(s, deal, value or {})
            elif name == "fit_checklist":
                value = await _clean_fit(s, deal, value or {})
            else:
                value = _jsonable(value)
            values[name] = value
        if (
            "expected_close_date" in sent
            and values["expected_close_date"] is None
            and here
            and prospecting
            and here.position >= prospecting.position
        ):
            raise ValidationFailed(
                "The expected close date is required from Prospecting on",
                {"field": "expected_close_date"},
            )

        # which permissions does this edit need?
        keys: list[str] = []
        changed = {n for n, v in values.items() if v != before[n]}
        if "lead_source" in changed and entry is not None and deal.stage_key != entry.key:
            keys.append("deal.source.change_after_lead")
        if "expected_close_date" in changed and before["expected_close_date"] is not None:
            keys.append("deal.close_date.change")
        if (
            changed & set(PRICING_FIELDS)
            and here is not None
            and prospecting is not None
            and here.position > prospecting.position
        ):
            keys.append("deal.pricing.change_after_prospecting")
        for k in keys:
            await uow.authorize(k, entity=entity, owner_id=deal.owner_id)
        cleaned = uow.reason("deal.edit", patch.reason)
        for k in keys:
            cleaned = uow.reason(k, patch.reason) or cleaned
        if patch.expected_version is not None and patch.expected_version != deal.version:
            raise Conflict(
                "Someone else changed this deal. Reload and try again.",
                {"current_version": deal.version},
            )
        if not changed:
            return deal.id, None
        for name in changed:
            _set(deal, step, name, values[name])
        after = snapshot(deal, step)
        diff = {k: (before[k], after[k]) for k in after if before[k] != after[k]}
        deal.version, deal.updated_at = deal.version + 1, utc_now()
        await s.flush()
        health.apply(deal, step)
        await recheck_proofs(uow, deal)
        audit_id = await uow.audit(
            keys[0] if keys else "deal.edit",
            "deal",
            deal.id,
            old={k: v[0] for k, v in diff.items()},
            new={k: v[1] for k, v in diff.items()},
            reason=cleaned,
        )
        await uow.emit(events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))
        return deal.id, audit_id


async def undo_patch(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, audit_id: uuid.UUID
) -> uuid.UUID:
    """Undo = a compensating edit with its own audit row, within 10 seconds, by the same person,
    only if the fields still hold the values that edit wrote."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        step = await open_step(s, deal.id, lock=True)
        await uow.authorize("deal.edit", entity=("deal", deal.id), owner_id=deal.owner_id)
        if deal.status in ("lost", "won"):  # a closed deal is reopened before it is edited
            raise Conflict("This deal is closed, so the change cannot be undone")
        row = await s.get(AuditLog, audit_id)
        if (
            row is None
            or row.org_id != actor.org_id
            or row.entity_type != "deal"
            or row.entity_id != str(deal.id)
            or row.outcome != "ok"
            or row.action_key not in EDIT_KEYS
        ):
            raise NotFound("That change cannot be undone")
        if row.actor_id != actor.id:
            raise PermissionDenied("You can only undo your own edit")
        if utc_now() - row.occurred_at > timedelta(seconds=UNDO_WINDOW_SECONDS):
            raise Conflict("The 10-second Undo window has passed")
        older, newer = row.old_value or {}, row.new_value or {}
        if not set(older) | set(newer) <= set(ALL_FIELDS):
            raise Conflict("This change cannot be undone")
        if (
            await s.execute(
                select(AuditLog.id).where(
                    AuditLog.entity_id == str(deal.id),
                    AuditLog.action_key == "deal.undo",
                    AuditLog.note == f"undoes {audit_id}",
                )
            )
        ).first():
            raise Conflict("This change was already undone")
        current = snapshot(deal, step)
        if any(current.get(k) != v for k, v in newer.items()):
            raise Conflict("This field was changed again, so it cannot be undone safely")
        for k, v in older.items():
            _set(deal, step, k, v)
        deal.version, deal.updated_at = deal.version + 1, utc_now()
        await s.flush()
        health.apply(deal, step)
        await recheck_proofs(uow, deal)
        new_id = await uow.audit(
            "deal.undo",
            "deal",
            deal.id,
            old={k: current.get(k) for k in newer},
            new=older,
            note=f"undoes {audit_id}",
        )
        await uow.emit(events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))
        return new_id
