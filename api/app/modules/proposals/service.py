# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Proposal template and automated drafting, editing, PDF, email, statuses, versions (P4-04).

Drafting freezes everything into `content`: nothing it reads later (a catalog price change, a
template edit) can change a proposal already drafted. Sending needs `proposal.send_list_price`
and is blocked while any deal line has a pending discount approval (Phase 3 rule).
"""

import html
import re
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import format_rupees
from app.core.time import utc_now
from app.modules.accounts.models import Account, Contact
from app.modules.catalog.models import DealProduct, Product
from app.modules.deals.common import load_deal
from app.modules.leads.models import Deal
from app.modules.proposals.models import Proposal, ProposalItem, ProposalTemplate
from app.modules.proposals.pdf import render_pdf
from app.platform.context import Actor, RequestInfo
from app.platform.files.models import FileRecord
from app.platform.files.storage import get_storage
from app.platform.notifications import email
from app.platform.notifications import service as notifications
from app.platform.permissions.guard import Effect
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

TEMPLATE_KEY = "catalog.template.manage"
SEND_KEY = "proposal.send_list_price"
VIEW_KEY = "deal.view"
DRAFT_KEY = "deal.edit"
PATCH_KEY = "deal.edit"
STATUS_KEY = "deal.edit"
PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
REVISION_REQUIRED_TYPE = "proposal.revision_required"
PENDING_TYPE = "proposal.pending"
PENDING_NO_FEEDBACK = timedelta(days=2)


# ---------------------------------------------------------------------------- template
async def active_template(session: AsyncSession, org_id: uuid.UUID) -> ProposalTemplate | None:
    return (
        (
            await session.execute(
                select(ProposalTemplate)
                .where(ProposalTemplate.org_id == org_id, ProposalTemplate.active.is_(True))
                .order_by(ProposalTemplate.version.desc())
            )
        )
        .scalars()
        .first()
    )


async def save_template(
    actor: Actor, request: RequestInfo, name: str, body: str, terms_html: str, reason: str | None
) -> dict[str, Any]:
    """Every save is a new version; the newest becomes active (older ones stay for proposals
    already built from them)."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(TEMPLATE_KEY)
        cleaned = uow.reason(TEMPLATE_KEY, reason)
        n = (
            await s.execute(
                select(ProposalTemplate.version)
                .where(ProposalTemplate.org_id == actor.org_id, ProposalTemplate.name == name)
                .order_by(ProposalTemplate.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none() or 0
        await s.execute(
            sa_update(ProposalTemplate)
            .where(ProposalTemplate.org_id == actor.org_id)
            .values(active=False)
        )
        tpl = ProposalTemplate(
            org_id=actor.org_id,
            name=name,
            body=body,
            terms_html=terms_html,
            version=n + 1,
            active=True,
            created_by=actor.id,
        )
        s.add(tpl)
        await s.flush()
        await uow.audit(
            TEMPLATE_KEY, "proposal_template", tpl.id,
            new={"name": name, "version": tpl.version}, reason=cleaned,
        )  # fmt: skip
        out = _template_out(tpl)
    return out


def _template_out(t: ProposalTemplate) -> dict[str, Any]:
    return {"id": t.id, "name": t.name, "body": t.body, "terms_html": t.terms_html,
            "version": t.version, "active": t.active}  # fmt: skip


async def read_template(session: AsyncSession, org_id: uuid.UUID) -> dict[str, Any] | None:
    tpl = await active_template(session, org_id)
    return _template_out(tpl) if tpl else None


# ---------------------------------------------------------------------------- drafting
async def _billing_contact_email(session: AsyncSession, deal: Deal) -> str | None:
    """The deal's named billing contact's email, matched by name or email; if the field names an
    address directly, that address; otherwise the account's first contact with an email on file."""
    rows: list[tuple[str | None, str | None, bool]] = [
        (email_, name, dm)
        for email_, name, dm in (
            await session.execute(
                select(Contact.email, Contact.name, Contact.is_decision_maker).where(
                    Contact.account_id == deal.account_id, Contact.deleted_at.is_(None)
                )
            )
        ).all()
    ]
    text: str | None = (deal.data or {}).get("billing_contact")
    if text:
        needle = text.strip().lower()
        for email_, name, _ in rows:
            if needle in ((email_ or "").lower(), (name or "").lower()):
                return email_
        if "@" in text:
            return text
    with_email = [(em, dm) for em, _, dm in rows if em]
    with_email.sort(key=lambda r: not r[1])
    return with_email[0][0] if with_email else None


async def _frozen_content(
    session: AsyncSession, deal: Deal
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    account = await session.get(Account, deal.account_id)
    rows = (
        await session.execute(
            select(DealProduct, Product)
            .join(Product, Product.id == DealProduct.product_id)
            .where(DealProduct.deal_id == deal.id, DealProduct.deleted_at.is_(None))
        )
    ).all()
    items = [
        {
            "product_id": p.id,
            "description": p.name,
            "quantity": dp.quantity,
            "unit_price_paise": dp.unit_price_paise,
            "discount_bp": int(dp.discount_pct * 100),
            "line_total_paise": dp.line_total_paise,
        }
        for dp, p in rows
    ]
    json_items = [{**i, "product_id": str(i["product_id"])} for i in items]
    content = {
        "company": account.name if account else "",
        "pricing_model": deal.pricing_model,
        "plan": deal.plan,
        "expected_minutes_per_month": deal.expected_minutes_per_month,
        "contract_term": (deal.data or {}).get("contract_term"),
        "billing_frequency": (deal.data or {}).get("billing_frequency"),
        "scope": (deal.data or {}).get("scope"),
        "value_paise": sum(cast(int, i["line_total_paise"]) for i in items),
        "items": json_items,
    }
    return content, items


def _items_table(items: Sequence[dict[str, Any]]) -> str:
    """The priced lines as a small HTML table for the proposal text (descriptions escaped)."""
    if not items:
        return "<p>Pricing to be confirmed.</p>"
    rows = "".join(
        f"<tr><td>{html.escape(str(i['description']))}</td><td>{i['quantity']}</td>"
        f"<td>{format_rupees(int(i['unit_price_paise']))}</td>"
        f"<td>{int(i['discount_bp']) / 100:g}%</td>"
        f"<td>{format_rupees(int(i['line_total_paise']))}</td></tr>"
        for i in items
    )
    return (
        '<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">'
        "<tr><th>Item</th><th>Qty</th><th>Unit price</th><th>Discount</th><th>Total</th></tr>"
        f"{rows}</table>"
    )


def _priced_table(items: Sequence[dict[str, Any]], tax_percent: float) -> str:
    """Products and pricing: each line, then the subtotal, the tax and the total."""
    rows = "".join(
        f"<tr><td>{html.escape(str(i['description']))}</td><td>{i['quantity']}</td>"
        f"<td>{format_rupees(int(i['unit_price_paise']))}</td>"
        f"<td>{format_rupees(int(i['line_total_paise']))}</td></tr>"
        for i in items
    )
    sub = sum(int(i["line_total_paise"]) for i in items)
    tax = round(sub * tax_percent / 100)
    foot = (
        f"<tr><td colspan='3'>Subtotal</td><td>{format_rupees(sub)}</td></tr>"
        f"<tr><td colspan='3'>Tax ({tax_percent:g}%)</td><td>{format_rupees(tax)}</td></tr>"
        f"<tr><td colspan='3'><b>Total</b></td><td><b>{format_rupees(sub + tax)}</b></td></tr>"
    )
    return (
        "<table><thead><tr><th>Product</th><th>Qty</th><th>Price per unit</th><th>Amount</th>"
        f"</tr></thead><tbody>{rows}{foot}</tbody></table>"
    )


def _structured_html(t: Any, recipient: str, items: Sequence[dict[str, Any]]) -> tuple[str, str]:
    """The proposal text from the accepted terms: who it is for, the scope, the commercials, the
    priced products, then the terms and conditions (returned separately)."""
    minutes = (
        f"{t.min_committed_minutes:,}" if t.min_committed_minutes is not None else "Not applicable"
    )
    commercials = [
        ("Business model", t.business_model),
        ("Billing frequency", t.billing_frequency),
        ("Minimum committed minutes per month", minutes),
        ("Concurrency (calls at the same time)", str(t.concurrency)),
        ("Tax", f"{t.tax_percent:g}%"),
    ]
    rows = "".join(
        f"<tr><td><b>{html.escape(k)}</b></td><td>{html.escape(v)}</td></tr>"
        for k, v in commercials
    )
    to = f"<p>Prepared for {html.escape(recipient)}.</p>" if recipient else ""
    details = f"<p>{_text_html(t.company_details)}</p>" if t.company_details else ""
    body = (
        f"<h1>Proposal for {html.escape(t.company_name)}</h1>{to}{details}"
        f"<h2>Scope of work</h2><p>{_text_html(t.scope_of_work)}</p>"
        f"<h2>Commercials</h2><table>{rows}</table>"
        f"<h2>Products and pricing</h2>{_priced_table(items, t.tax_percent)}"
    )
    terms = f"<h2>Terms and conditions</h2><p>{_text_html(t.terms_and_conditions)}</p>"
    return body, terms


def _text_html(text: str) -> str:
    """What the rep typed, safe to put in the proposal: line breaks kept, nothing else."""
    return html.escape(text).replace(chr(10), "<br>")


def _fill_template(body: str, content: dict[str, Any]) -> str:
    def sub(m: re.Match[str]) -> str:
        value: Any = content
        for part in m.group(1).split("."):
            value = value.get(part) if isinstance(value, dict) else None
            if value is None:
                return ""
        return str(value)

    return PLACEHOLDER.sub(sub, body)


def _out(p: Proposal, items: Sequence[ProposalItem]) -> dict[str, Any]:
    return {
        "id": p.id,
        "deal_id": p.deal_id,
        "version_no": p.version_no,
        "template_version": p.template_version,
        "content": p.content,
        "status": p.status,
        "pdf_file_id": p.pdf_file_id,
        "sent_at": p.sent_at,
        "sent_to": p.sent_to,
        "feedback": p.feedback,
        "items": [
            {
                "id": i.id,
                "product_id": i.product_id,
                "description": i.description,
                "quantity": i.quantity,
                "unit_price_paise": i.unit_price_paise,
                "discount_bp": i.discount_bp,
                "line_total_paise": i.line_total_paise,
            }
            for i in items
        ],
    }


async def _lines_from_terms(session: AsyncSession, deal: Deal, t: Any) -> list[dict[str, Any]]:
    """The proposal's lines from what the rep entered. A price below what was approved for that
    product on the deal is refused: a lower price goes through the discount approval on the deal."""
    approved: dict[uuid.UUID, int] = {}
    for dp in (
        await session.execute(
            select(DealProduct).where(
                DealProduct.deal_id == deal.id, DealProduct.deleted_at.is_(None)
            )
        )
    ).scalars():
        approved[dp.product_id] = dp.line_total_paise // max(dp.quantity, 1)
    out: list[dict[str, Any]] = []
    for ln in t.lines:
        pid = ln.product_id if ln.product_id in approved else None
        if pid is not None and ln.unit_price_paise < approved[pid]:
            raise ValidationFailed(
                f"{ln.description}: the price per unit is below the approved price "
                f"({format_rupees(approved[pid])}). A lower price needs a discount approval "
                "on the deal.",
                {"field": "lines"},
            )
        out.append(
            {
                "product_id": pid,
                "description": ln.description.strip(),
                "quantity": ln.quantity,
                "unit_price_paise": ln.unit_price_paise,
                "discount_bp": 0,
                "line_total_paise": ln.quantity * ln.unit_price_paise,
            }
        )
    return out


async def draft_proposal(
    actor: Actor,
    request: RequestInfo,
    deal_id: uuid.UUID,
    reason: str | None,
    recipient_name: str | None = None,
    recipient_email: str | None = None,
    written: Any = None,
) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id)
        await uow.authorize(DRAFT_KEY, entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason(DRAFT_KEY, reason)
        template = await active_template(s, actor.org_id)
        if template is None and written is None:
            raise NotFound("No active proposal template; ask an Admin to create one")
        name = (recipient_name or "").strip()
        address = (recipient_email or "").strip()
        if address and not re.fullmatch(r"\S+@\S+\.\S+", address):
            raise ValidationFailed(
                "Check the recipient's email address", {"field": "recipient_email"}
            )
        content, items = await _frozen_content(s, deal)
        content["recipient_name"], content["recipient_email"] = name, address
        if written is not None:
            items = await _lines_from_terms(s, deal, written)
            value = sum(cast(int, i["line_total_paise"]) for i in items)
            content["value_paise"] = value
            content["pricing_model"] = written.business_model
            content["billing_frequency"] = written.billing_frequency
            content["scope"] = written.scope_of_work
            content["items"] = [
                {**i, "product_id": str(i["product_id"]) if i["product_id"] else None}
                for i in items
            ]
            content["terms"] = written.model_dump(mode="json")
            content["total_inr"] = format_rupees(round(value * (1 + written.tax_percent / 100)))
            content["body_html"], content["terms_html"] = _structured_html(written, name, items)
        else:  # the older route: the admin's template, filled in
            content["items_html"] = _items_table(items)
            content["total_inr"] = format_rupees(int(content["value_paise"]))
            assert template is not None
            content["terms_html"] = template.terms_html
            content["body_html"] = _fill_template(template.body, content)
        n = (
            await s.execute(
                select(Proposal.version_no)
                .where(Proposal.deal_id == deal.id)
                .order_by(Proposal.version_no.desc())
                .limit(1)
            )
        ).scalar_one_or_none() or 0
        proposal = Proposal(
            org_id=actor.org_id,
            deal_id=deal.id,
            version_no=n + 1,
            template_version=template.version if template else None,
            content=content,
            status="draft",
            created_by=actor.id,
        )
        s.add(proposal)
        await s.flush()
        for it in items:
            s.add(
                ProposalItem(
                    org_id=actor.org_id,
                    proposal_id=proposal.id,
                    product_id=it["product_id"],
                    description=it["description"],
                    quantity=it["quantity"],
                    unit_price_paise=it["unit_price_paise"],
                    discount_bp=it["discount_bp"],
                    line_total_paise=it["line_total_paise"],
                )
            )
        await s.flush()
        await uow.audit(
            DRAFT_KEY, "proposal", proposal.id,
            new={"deal_id": str(deal.id), "version_no": proposal.version_no}, reason=cleaned,
        )  # fmt: skip
        rows = (
            (await s.execute(select(ProposalItem).where(ProposalItem.proposal_id == proposal.id)))
            .scalars()
            .all()
        )
        out = _out(proposal, rows)
    return out


async def _load(
    session: AsyncSession, proposal_id: uuid.UUID
) -> tuple[Proposal, Sequence[ProposalItem]]:
    p = await session.get(Proposal, proposal_id)
    if p is None or p.deleted_at is not None:
        raise NotFound("Proposal not found")
    items = (
        (await session.execute(select(ProposalItem).where(ProposalItem.proposal_id == p.id)))
        .scalars()
        .all()
    )
    return p, items


async def get_proposal(
    session: AsyncSession, actor: Actor, proposal_id: uuid.UUID
) -> dict[str, Any]:
    p, items = await _load(session, proposal_id)
    deal = await session.get(Deal, p.deal_id)
    assert deal is not None
    await authorize_read(session, actor, VIEW_KEY, entity=("deal", deal.id))
    return _out(p, items)


async def patch_proposal(
    actor: Actor,
    request: RequestInfo,
    proposal_id: uuid.UUID,
    content: dict[str, Any] | None,
    reason: str | None,
) -> dict[str, Any]:
    """The rep edits the draft: text fields and line items (within discount rules, enforced
    when the lines themselves are changed via the catalog endpoints)."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        p, items = await _load(s, proposal_id)
        deal = await load_deal(uow, p.deal_id)
        await uow.authorize(PATCH_KEY, entity=("deal", deal.id), owner_id=deal.owner_id)
        if p.status not in ("draft", "changes_requested"):
            raise Conflict("Only a draft, or a proposal with changes requested, can be edited")
        cleaned = uow.reason(PATCH_KEY, reason)
        old = dict(p.content)
        if content is not None:
            p.content = {**p.content, **content}
        p.updated_at = utc_now()
        await s.flush()
        await uow.audit(PATCH_KEY, "proposal", p.id, old=old, new=p.content, reason=cleaned)
        out = _out(p, items)
    return out


# ---------------------------------------------------------------------------- send
async def _pending_discount_blocks(session: AsyncSession, deal_id: uuid.UUID) -> bool:
    row = await session.execute(
        select(DealProduct.id).where(
            DealProduct.deal_id == deal_id, DealProduct.discount_status == "pending"
        )
    )
    return row.first() is not None


async def send_proposal(
    actor: Actor,
    request: RequestInfo,
    proposal_id: uuid.UUID,
    to: str | None,
    reason: str | None,
    to_name: str | None = None,
) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        p, items = await _load(s, proposal_id)
        deal = await load_deal(uow, p.deal_id)
        decision = await uow.authorize(SEND_KEY, entity=("deal", deal.id), owner_id=deal.owner_id)
        if decision.effect != Effect.ALLOW:
            raise ValidationFailed("Sending needs an approved discount first")
        if await _pending_discount_blocks(s, deal.id):
            raise ValidationFailed("A discount on this deal is still awaiting approval")
        if p.status not in ("draft", "changes_requested"):
            raise Conflict("Only a draft, or a proposal with changes requested, can be sent")
        cleaned = uow.reason(SEND_KEY, reason)
        recipient = to or p.content.get("recipient_email") or await _billing_contact_email(s, deal)
        if not recipient:
            raise ValidationFailed("No recipient email: enter who the proposal is for")
        if to_name and to_name.strip() != p.content.get("recipient_name"):
            p.content = {**p.content, "recipient_name": to_name.strip()}

        html_body = f"{p.content.get('body_html', '')}<hr>{p.content.get('terms_html', '')}"
        pdf_bytes = render_pdf(html_body)
        key = f"{actor.org_id}/proposals/{p.id}/v{p.version_no}.pdf"
        await get_storage().put_bytes(key, pdf_bytes, "application/pdf")
        file_rec = FileRecord(
            org_id=actor.org_id,
            owner_entity_type="deal",
            owner_entity_id=str(deal.id),
            key=key,
            filename=f"proposal-v{p.version_no}.pdf",
            content_type="application/pdf",
            size=len(pdf_bytes),
            status="uploaded",
            uploaded_by=actor.id,
        )
        s.add(file_rec)
        await s.flush()

        backend = email.get_backend()
        subject = f"Proposal: {p.content.get('company', 'your company')}"
        text_body = re.sub(r"<[^>]+>", " ", html_body)
        message_id = f"proposal-{p.id}-v{p.version_no}"
        await backend.send(
            recipient, subject, html_body, text_body,
            [("proposal.pdf", "application/pdf", pdf_bytes)],
        )  # fmt: skip

        p.status, p.pdf_file_id = "sent", file_rec.id
        p.sent_at, p.sent_to, p.email_message_id = utc_now(), recipient, message_id
        await s.flush()
        await uow.audit(
            SEND_KEY, "proposal", p.id,
            new={"sent_to": recipient, "pdf_file_id": str(file_rec.id)}, reason=cleaned,
        )  # fmt: skip
        out = _out(p, items)
        deal_id, pdf_file_id = deal.id, file_rec.id
    # the documents record is created in its own transaction, once the proposal's file row above
    # has actually committed (a nested UnitOfWork here would not yet see it).
    from app.modules.documents import schemas as doc_schemas
    from app.modules.documents import service as documents_service

    await documents_service.add_document(
        actor,
        request,
        deal_id,
        doc_schemas.DocumentIn(kind="proposal", status="sent", file_id=pdf_file_id, reason=cleaned),
    )
    return out


async def set_status(
    actor: Actor, request: RequestInfo, proposal_id: uuid.UUID, status: str, feedback: str | None,
    reason: str | None,
) -> dict[str, Any]:  # fmt: skip
    """Accepted / Changes requested / Rejected. The feedback fills the Proposal stage's
    'Customer feedback recorded' field (deal.data.feedback)."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        p, items = await _load(s, proposal_id)
        deal = await load_deal(uow, p.deal_id)
        await uow.authorize(STATUS_KEY, entity=("deal", deal.id), owner_id=deal.owner_id)
        if p.status != "sent":
            raise Conflict("Only a sent proposal can change status this way")
        cleaned = uow.reason(STATUS_KEY, reason)
        old = {"status": p.status}
        p.status, p.feedback, p.updated_at = status, feedback, utc_now()
        if feedback:
            deal.data = {**(deal.data or {}), "feedback": feedback}
        await s.flush()
        await uow.audit(
            STATUS_KEY,
            "proposal",
            p.id,
            old=old,
            new={"status": status, "feedback": feedback},
            reason=cleaned,
        )
        if status == "changes_requested" and deal.owner_id:
            await notifications.notify(
                s,
                deal.org_id,
                [deal.owner_id],
                REVISION_REQUIRED_TYPE,
                "Proposal needs revision",
                (feedback or "The customer requested changes.").strip(),
                f"/deals/{deal.id}",
                dedupe_key=f"{REVISION_REQUIRED_TYPE}:{p.id}:{p.updated_at.isoformat()}",
            )
        out = _out(p, items)
    return out


async def check_pending_no_feedback(
    session: AsyncSession, org_id: uuid.UUID, now: Any = None
) -> int:
    """Daily: a proposal that has sat in 'sent' status for 2+ days with no customer feedback
    notifies the deal owner once (PRD 11 'Proposal pending', P5-08). Dedup is `notify()`'s own
    dedupe_key, keyed by proposal id without a date, so it fires exactly once per proposal."""
    now = now or utc_now()
    cutoff = now - PENDING_NO_FEEDBACK
    rows = (
        await session.execute(
            select(Proposal, Deal)
            .join(Deal, Deal.id == Proposal.deal_id)
            .where(
                Proposal.org_id == org_id,
                Proposal.status == "sent",
                Proposal.deleted_at.is_(None),
                Proposal.sent_at.is_not(None),
                Proposal.sent_at <= cutoff,
            )
        )
    ).all()
    sent = 0
    for p, deal in rows:
        if deal.owner_id is None or p.sent_at is None:
            continue
        sent += await notifications.notify(
            session,
            org_id,
            [deal.owner_id],
            PENDING_TYPE,
            "Proposal pending customer feedback",
            f"The proposal sent on {p.sent_at.date().isoformat()} has had no response for "
            f"{PENDING_NO_FEEDBACK.days} days.",
            f"/deals/{deal.id}",
            dedupe_key=f"{PENDING_TYPE}:{p.id}",
        )
    return sent


async def versions(session: AsyncSession, actor: Actor, deal_id: uuid.UUID) -> list[dict[str, Any]]:
    deal = await session.get(Deal, deal_id)
    if deal is None:
        raise NotFound("Deal not found")
    await authorize_read(session, actor, VIEW_KEY, entity=("deal", deal.id))
    rows = (
        await session.execute(
            select(Proposal).where(Proposal.deal_id == deal_id).order_by(Proposal.version_no)
        )
    ).scalars()
    out = []
    for p in rows:
        items = (
            (await session.execute(select(ProposalItem).where(ProposalItem.proposal_id == p.id)))
            .scalars()
            .all()
        )
        out.append(_out(p, items))
    return out


async def prefill(session: AsyncSession, actor: Actor, deal_id: uuid.UUID) -> dict[str, Any]:
    """What the proposal form starts with: the deal's products at their approved prices, the
    business model and minutes from Prospecting, the company, and (when a proposal was drafted
    before) the terms of the latest one so a revision starts from them."""
    deal = await session.get(Deal, deal_id)
    if deal is None or deal.deleted_at is not None:
        raise NotFound("Deal not found")
    await authorize_read(session, actor, VIEW_KEY, entity=("deal", deal.id))
    account = await session.get(Account, deal.account_id)
    rows = (
        await session.execute(
            select(DealProduct, Product)
            .join(Product, Product.id == DealProduct.product_id)
            .where(DealProduct.deal_id == deal.id, DealProduct.deleted_at.is_(None))
        )
    ).all()
    latest = (
        await session.execute(
            select(Proposal)
            .where(Proposal.deal_id == deal.id, Proposal.deleted_at.is_(None))
            .order_by(Proposal.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    terms = (latest.content or {}).get("terms") if latest else None
    return {
        "company_name": account.name if account else "",
        "business_model": deal.pricing_model,
        "min_committed_minutes": deal.expected_minutes_per_month,
        "scope_of_work": (deal.data or {}).get("scope") or deal.use_case,
        "lines": [
            {
                "product_id": p.id,
                "description": p.name,
                "quantity": dp.quantity,
                "unit_price_paise": dp.line_total_paise // max(dp.quantity, 1),
            }
            for dp, p in rows
        ],
        "last_terms": terms if isinstance(terms, dict) else None,
    }


def register() -> None:
    pass
