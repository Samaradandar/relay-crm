# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Proof validators: what must exist before an exit criterion can be ticked.

A criterion names a proof type and a config (see seeds/new_business.py). The server checks the
proof itself; the screen never decides. Validators are registered by type, so a later phase adds
a type without touching the stage engine.

`recheck=True` is used when a tick is re-examined later (a proof source was edited or deleted).
Time-based conditions (a meeting that had to be in the future, a call that had to be completed)
are only judged at tick time; later we only ask "does the proof still exist and still qualify".
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.modules.accounts.models import Account, Contact
from app.modules.activities.models import DEAD_MEETING_STATUSES, MEETING_STATUSES, Activity
from app.modules.catalog.models import DealProduct
from app.modules.documents.models import Document
from app.modules.leads.models import Deal
from app.modules.pipeline.config.models import FitItem
from app.modules.proposals.models import Proposal, ProposalRound

LABELS = {
    "account.name": "company name",
    "account.website": "website",
    "account.industry": "industry",
    "contact.name": "contact name",
    "contact.phone": "contact phone",
    "contact.email": "contact email",
    "lead_source": "lead source",
    "pain_point": "pain point",
    "use_case": "use case",
    "budget_amount_paise": "budget amount",
    "budget_source": "budget source",
    "expected_close_date": "expected close date",
    "expected_close_reason": "reason for the close date",
    "pricing_model": "pricing model",
    "plan": "plan",
    "expected_minutes_per_month": "expected minutes per month",
    "feedback": "customer feedback",
    "decision_date": "decision date",
    "final_price_paise": "final price",
    "scope": "agreed scope",
    "agreed_by": "who agreed",
    "billing_contact": "billing contact",
    "subscription_start_date": "subscription start date",
    "contract_term": "contract term",
    "billing_frequency": "billing frequency",
    "renewal_date": "renewal or contract end date",
    "go_live_date": "go-live date",
    "live_date": "live date",
    "live_note": "live note",
    "prereq_documents": "what the pre-requisite document covers",
    "prereq_received_on": "date the pre-requisite document was received",
    "technical_documents": "what the technical document covers",
    "technical_received_on": "date the technical document was received",
    "advance_amount_paise": "advance amount",
    "advance_received_by": "who received the advance",
    "advance_received_contact": "contact details of who received the advance",
    "advance_received_on": "date the advance was received",
}


@dataclass
class ProofResult:
    ok: bool
    ref: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


@dataclass
class ProofCtx:
    session: AsyncSession
    deal: Deal
    account: Account
    contacts: list[Contact]
    primary: Contact | None
    now: datetime
    recheck: bool = False


async def build_ctx(session: AsyncSession, deal: Deal, *, recheck: bool = False) -> ProofCtx:
    account = await session.get(Account, deal.account_id)
    assert account is not None
    contacts = list(
        (
            await session.execute(
                select(Contact).where(
                    Contact.account_id == deal.account_id, Contact.deleted_at.is_(None)
                )
            )
        ).scalars()
    )
    primary = next((c for c in contacts if c.id == deal.primary_contact_id), None)
    return ProofCtx(session, deal, account, contacts, primary, utc_now(), recheck)


# ---------------------------------------------------------------------------- field access
def field_value(ctx: ProofCtx, name: str) -> Any:
    if name.startswith("account."):
        return getattr(ctx.account, name.split(".", 1)[1], None)
    if name.startswith("contact."):
        attr = name.split(".", 1)[1]
        attr = "phone_e164" if attr == "phone" else attr
        return getattr(ctx.primary, attr, None) if ctx.primary else None
    if name in Deal.__table__.columns and name != "data":
        return getattr(ctx.deal, name)
    if name in (ctx.deal.data or {}):
        return ctx.deal.data[name]
    return (ctx.deal.custom_fields or {}).get(name)


def filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, list | dict):
        return len(value) > 0
    return True


def label(name: str) -> str:
    return LABELS.get(name, name.replace("_", " "))


# ---------------------------------------------------------------------------- validators
Validator = Callable[[ProofCtx, dict[str, Any], dict[str, Any] | None], Awaitable[ProofResult]]
VALIDATORS: dict[str, Validator] = {}


def register_proof(type_: str, fn: Validator) -> None:
    VALIDATORS[type_] = fn


async def _fields(ctx: ProofCtx, cfg: dict[str, Any], hint: dict[str, Any] | None) -> ProofResult:
    missing: list[str] = []
    for req in cfg.get("require", []):
        if isinstance(req, dict):
            options = req.get("any", [])
            if not any(filled(field_value(ctx, n)) for n in options):
                missing.append(" or ".join(label(n) for n in options))
        elif not filled(field_value(ctx, req)):
            missing.append(label(req))
    return ProofResult(not missing, {}, [f"Fill in: {m}" for m in missing])


async def _activity(ctx: ProofCtx, cfg: dict[str, Any], hint: dict[str, Any] | None) -> ProofResult:
    q = select(Activity).where(
        Activity.deal_id == ctx.deal.id,
        Activity.deleted_at.is_(None),
        Activity.type.in_(cfg.get("types", [])),
    )
    # a meeting that was postponed, cancelled or never happened is never proof, in any version
    q = q.where(
        or_(
            Activity.meeting_status.is_(None), Activity.meeting_status.not_in(DEAD_MEETING_STATUSES)
        )
    )
    if cfg.get("meeting_status"):  # e.g. ["booked", "held"] to arrange, ["held"] once it happened
        q = q.where(Activity.meeting_status.in_(cfg["meeting_status"]))
    if cfg.get("completed"):  # an older rule: a booking whose date passed is still not held
        q = q.where(or_(Activity.meeting_status.is_(None), Activity.meeting_status != "booked"))
    if cfg.get("after_last_round"):  # Proposal: each round needs its own meeting
        # logged in this stage: the Meeting scheduled stage's meeting does not count again
        q = q.where(Activity.created_at >= ctx.deal.stage_entered_at)
        cutoff = (
            await ctx.session.execute(
                select(func.max(ProposalRound.closed_at)).where(
                    ProposalRound.deal_id == ctx.deal.id
                )
            )
        ).scalar_one()
        if cutoff is not None:
            q = q.where(Activity.occurred_at > cutoff)
    if cfg.get("two_way"):
        q = q.where(Activity.two_way.is_(True))
    if cfg.get("attendees"):
        q = q.where(func.jsonb_array_length(Activity.attendees) > 0)
    if cfg.get("notes"):
        q = q.where(func.coalesce(func.length(func.trim(Activity.discussed)), 0) > 0)
    if not ctx.recheck:
        if cfg.get("future"):
            q = q.where(Activity.occurred_at > ctx.now)
        if cfg.get("completed"):
            q = q.where(Activity.occurred_at <= ctx.now)
    rows = list((await ctx.session.execute(q.order_by(Activity.occurred_at.desc()))).scalars())
    if hint and hint.get("activity_id"):
        wanted = str(hint["activity_id"])
        pick = next((a for a in rows if str(a.id) == wanted), None)
        if pick is None and not ctx.recheck:
            return ProofResult(False, {}, ["The chosen activity does not qualify as proof"])
    else:
        pick = None
    pick = pick or (rows[0] if rows else None)
    if pick is None:
        kinds = " / ".join(cfg.get("types", []))
        statuses = cfg.get("meeting_status")
        if statuses == ["held"]:
            return ProofResult(
                False, {}, ["Mark the booked meeting as held (who attended and what was discussed)"]
            )
        if statuses:
            return ProofResult(
                False, {}, ["Book a meeting with a date and attendees, or log one that was held"]
            )
        extras = [
            w
            for w, on in (
                ("two-way", cfg.get("two_way")),
                ("with attendees", cfg.get("attendees")),
                ("with notes", cfg.get("notes")),
                ("dated in the future", cfg.get("future") and not ctx.recheck),
                ("already held", cfg.get("completed") and not ctx.recheck),
            )
            if on
        ]
        return ProofResult(
            False,
            {},
            [
                f"Log a {kinds} activity ({', '.join(extras)})"
                if extras
                else f"Log a {kinds} activity"
            ],
        )
    return ProofResult(True, {"activity_id": str(pick.id)})


async def _checklist(
    ctx: ProofCtx, cfg: dict[str, Any], hint: dict[str, Any] | None
) -> ProofResult:
    items = list(
        (
            await ctx.session.execute(
                select(FitItem).where(FitItem.org_id == ctx.deal.org_id, FitItem.active.is_(True))
            )
        ).scalars()
    )
    if not items:
        return ProofResult(False, {}, ["The fit checklist has no items yet. Ask the Super admin"])
    ticked = ctx.deal.fit_checklist or {}
    missing = [i.label for i in items if not ticked.get(str(i.id))]
    return ProofResult(not missing, {}, [f"Fit check not ticked: {m}" for m in missing])


async def _contact_tag(
    ctx: ProofCtx, cfg: dict[str, Any], hint: dict[str, Any] | None
) -> ProofResult:
    tag = cfg.get("tag", "decision_maker")
    attr = {"decision_maker": "is_decision_maker"}.get(tag)
    need = cfg.get("require", [])
    have = {"name": "name", "phone": "phone_e164", "email": "email"}

    def lacks(c: Any) -> list[str]:
        return [f for f in need if not filled(getattr(c, have[f], None))]

    tagged = [c for c in ctx.contacts if attr and getattr(c, attr)]
    pick = next((c for c in tagged if not lacks(c)), None)
    if pick is None and tagged:
        return ProofResult(
            False, {}, [f"The decision maker needs their {' and '.join(lacks(tagged[0]))}"]
        )
    if pick is None:
        return ProofResult(False, {}, [f"Tag a contact as {tag.replace('_', ' ')}"])
    return ProofResult(True, {"contact_id": str(pick.id)})


async def _deal_lines(
    ctx: ProofCtx, cfg: dict[str, Any], hint: dict[str, Any] | None
) -> ProofResult:
    n = (
        await ctx.session.execute(
            select(func.count())
            .select_from(DealProduct)
            .where(DealProduct.deal_id == ctx.deal.id, DealProduct.deleted_at.is_(None))
        )
    ).scalar_one()
    need = int(cfg.get("min_lines", 1))
    if n < need:
        return ProofResult(False, {}, ["Add a product from the catalog to the deal"])
    return ProofResult(True, {"lines": n})


async def _document(ctx: ProofCtx, cfg: dict[str, Any], hint: dict[str, Any] | None) -> ProofResult:
    q = select(Document).where(
        Document.deal_id == ctx.deal.id,
        Document.deleted_at.is_(None),
        Document.kind == cfg["kind"],
        Document.status.in_(cfg.get("status", ["draft", "sent", "countersigned"])),
    )
    if cfg.get("file"):
        q = q.where(Document.file_id.is_not(None))
    rows = list((await ctx.session.execute(q.order_by(Document.created_at.desc()))).scalars())
    if not rows:
        st = " or ".join(cfg.get("status", []))
        what = f"{cfg['kind']} with status {st}" if cfg.get("status") else cfg["kind"]
        return ProofResult(
            False, {}, [f"Add the {what}" + (" and attach the file" if cfg.get("file") else "")]
        )
    return ProofResult(True, {"document_id": str(rows[0].id)})


# change_type -> the new_plan_values keys it must carry (Phase 5 P5-02). Kept here (not in
# billing) because this is only ever used to validate the Closure criterion's proof shape.
CLOSURE_CHANGE_SHAPES: dict[str, tuple[str, ...]] = {
    "top_up": ("size_minutes", "price_paise"),
    "plan_change": ("pricing_model", "plan"),
    "increase": ("pricing_model", "plan"),
    "decrease": ("pricing_model", "plan"),
    "model_switch": ("pricing_model", "plan"),
    "other_product": ("product_id", "quantity"),
    "renewal": ("contract_term",),
}


async def _closure_change(
    ctx: ProofCtx, cfg: dict[str, Any], hint: dict[str, Any] | None
) -> ProofResult:
    """Closure's 'new plan and effective date recorded' criterion: `deals.closure_change` must
    be non-null, name a known change_type, carry an effective_date, and its new_plan_values must
    have the fields that change_type needs (see CLOSURE_CHANGE_SHAPES)."""
    change = ctx.deal.closure_change
    if not isinstance(change, dict):
        return ProofResult(False, {}, ["Record the new plan, change type and effective date"])
    change_type = change.get("change_type")
    shape = CLOSURE_CHANGE_SHAPES.get(change_type or "")
    if shape is None:
        return ProofResult(False, {}, ["Pick a valid change type"])
    if not filled(change.get("effective_date")):
        return ProofResult(False, {}, ["Set the effective date"])
    values = change.get("new_plan_values")
    if not isinstance(values, dict):
        return ProofResult(False, {}, ["Fill in the new plan values"])
    missing = [f for f in shape if not filled(values.get(f))]
    if missing:
        return ProofResult(False, {}, [f"New plan values missing: {', '.join(missing)}"])
    return ProofResult(True, {"change_type": change_type})


async def _proposal_round(
    ctx: ProofCtx, cfg: dict[str, Any], hint: dict[str, Any] | None
) -> ProofResult:
    """The customer's answer for the round being worked on (an open round: a change request
    closes its round, so only an accepted answer is ever open)."""
    want = cfg.get("feedback", "accepted")
    row = (
        await ctx.session.execute(
            select(ProposalRound)
            .where(ProposalRound.deal_id == ctx.deal.id, ProposalRound.closed_at.is_(None))
            .order_by(ProposalRound.round_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is not None and row.feedback == want:
        return ProofResult(True, {"round_id": str(row.id)})
    return ProofResult(
        False,
        {},
        ["Record the customer's answer: accepted (changes or objections start a new round)"],
    )


async def _proposal_sent(
    ctx: ProofCtx, cfg: dict[str, Any], hint: dict[str, Any] | None
) -> ProofResult:
    """The proposal was emailed: a draft was made and sent to a recipient."""
    statuses = cfg.get("statuses", ["sent", "accepted"])
    row = (
        await ctx.session.execute(
            select(Proposal)
            .where(
                Proposal.deal_id == ctx.deal.id,
                Proposal.deleted_at.is_(None),
                Proposal.status.in_(statuses),
                Proposal.sent_to.is_not(None),
            )
            .order_by(Proposal.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        return ProofResult(True, {"proposal_id": str(row.id)})
    return ProofResult(False, {}, ["Send the proposal by email (draft, recipient name and email)"])


for _t, _fn in (
    ("fields", _fields),
    ("field", _fields),
    ("activity", _activity),
    ("checklist", _checklist),
    ("contact_tag", _contact_tag),
    ("deal_lines", _deal_lines),
    ("document", _document),
    ("closure_change", _closure_change),
    ("proposal_round", _proposal_round),
    ("proposal_sent", _proposal_sent),
):
    register_proof(_t, _fn)


async def run_proof(
    ctx: ProofCtx, proof_type: str, config: dict[str, Any], hint: dict[str, Any] | None = None
) -> ProofResult:
    """Run the main check, then every check in `also`. All must pass."""
    fn = VALIDATORS.get(proof_type)
    if fn is None:
        return ProofResult(False, {}, [f"Unknown proof type {proof_type!r}"])
    result = await fn(ctx, config, hint)
    ref = dict(result.ref)
    missing = list(result.missing)
    for extra in config.get("also", []):
        sub = await run_proof(ctx, extra["type"], extra.get("config", {}), hint)
        ref.update(sub.ref)
        missing += sub.missing
    return ProofResult(not missing and result.ok, ref, missing)


DOCUMENT_KINDS = ("proposal", "agreement", "invoice", "advance", "prerequisite", "technical")


def valid_proof_config(proof_type: str, config: dict[str, Any]) -> str | None:
    """Static check used when a pipeline draft is validated. Returns a problem or None."""
    if proof_type not in VALIDATORS:
        return f"unknown proof type {proof_type!r}"
    if proof_type in ("fields", "field") and not config.get("require"):
        return "needs at least one field in 'require'"
    if proof_type == "activity" and not config.get("types"):
        return "needs at least one activity type in 'types'"
    unknown = set(config.get("meeting_status", [])) - set(MEETING_STATUSES)
    if proof_type == "activity" and unknown:
        return "'meeting_status' can only list booked, held, postponed, cancelled or no_show"
    if proof_type == "document" and config.get("kind") not in DOCUMENT_KINDS:
        return "needs a document 'kind' (" + ", ".join(DOCUMENT_KINDS) + ")"
    if proof_type == "contact_tag" and config.get("tag", "decision_maker") != "decision_maker":
        return "only the decision_maker tag exists"
    if proof_type == "contact_tag" and not set(config.get("require", [])) <= {
        "name",
        "phone",
        "email",
    }:
        return "'require' can only list name, phone and email"
    for extra in config.get("also", []):
        sub = valid_proof_config(extra.get("type", ""), extra.get("config", {}))
        if sub:
            return sub
    return None


def new_id() -> uuid.UUID:
    return uuid.uuid4()
