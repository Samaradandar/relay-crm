# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Product catalog, deal lines, value-change rules and discount approvals (PRD 11, P3-05..07).

The deal's value is derived: the sum of its lines (quantity x list price, less discount).
A line's unit price is frozen to the catalog list price when it is added; the discount is the only
price lever, so "below the floor price" is simply a discount that takes the price under the floor.

Two independent gates apply to a change of lines:
- value change, judged on what the value is at the moment of the edit
    up to 5%      deal.value.up_to_5      (owner, Team Leader, Admin, Super admin)
    5% to 10%     deal.value.5_to_10      (reason required)
    over 10%      deal.value.above_10     (User needs their TL's approval, TL needs Admin/SA)
- discount
    up to 10%     discount.up_to_10       (User needs TL approval)
    over 10% or below the floor   discount.above_10_or_below_floor  (Admin/SA approval)
If a gate says "approval", nothing of the change is applied except that an existing line shows
"Discount awaiting approval"; the discount only takes effect when it is approved.
"""

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import format_rupees
from app.core.time import utc_now
from app.modules.catalog.models import DealProduct, Product
from app.modules.deals.common import load_deal
from app.modules.leads.events import DEAL_UPDATED
from app.modules.leads.models import Deal
from app.modules.leads.ownership import company_name, deal_rt
from app.modules.pipeline.engine.service import recheck_proofs
from app.platform.approvals import service as approvals
from app.platform.approvals.models import ApprovalRequest
from app.platform.context import Actor, RequestInfo
from app.platform.permissions.guard import Decision, Effect
from app.platform.permissions.matrix import ROUTE_ADMIN_POOL
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

CATALOG_KEY = "catalog.template.manage"
VALUE_APPROVAL = "deal.value_change"
DISCOUNT_TL = "discount.up_to_10"
DISCOUNT_ADMIN = "discount.above_10_or_below_floor"
NEEDS_APPROVAL = (Effect.NEEDS_APPROVAL, Effect.REQUEST_ONLY)


def line_total(quantity: int, unit_price: int, discount_pct: Decimal) -> int:
    gross = Decimal(quantity) * Decimal(unit_price) * (Decimal(100) - Decimal(discount_pct)) / 100
    return int(gross.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def value_key(old: int, new: int) -> str | None:
    if new == old:
        return None
    if old <= 0:
        return "deal.value.up_to_5"
    pct = abs(Decimal(new - old)) * 100 / Decimal(old)
    if pct <= 5:
        return "deal.value.up_to_5"
    return "deal.value.5_to_10" if pct <= 10 else "deal.value.above_10"


def discount_key(discount_pct: Decimal, unit_price: int, floor: int) -> str:
    price = Decimal(unit_price) * (Decimal(100) - discount_pct) / 100
    return DISCOUNT_ADMIN if discount_pct > 10 or price < Decimal(floor) else DISCOUNT_TL


# ---------------------------------------------------------------------------- catalog
def _product_snapshot(row: Product) -> dict[str, Any]:
    """What the audit row records about a product: prices and the Phase 4 pricing terms."""
    return {
        "name": row.name,
        "list": row.list_price_paise,
        "floor": row.floor_price_paise,
        "active": row.active,
        "pricing_model": row.pricing_model,
        "pack_options": row.pack_options,
        "subscription_fees": row.subscription_fees,
        "allowed_contract_terms": row.allowed_contract_terms,
        "allowed_billing_frequencies": row.allowed_billing_frequencies,
        "one_time_fees": row.one_time_fees,
    }


def _apply_pricing(row: Product, it: Any) -> None:
    """Set the Phase 4 pricing terms. A client that does not send a field leaves it as it was
    (an older screen saving a name change must not wipe a SaaS product's fees)."""
    sent = it.model_fields_set
    if "pricing_model" in sent:
        row.pricing_model = it.pricing_model
    if "pack_options" in sent:
        row.pack_options = [
            p.model_dump() for p in sorted(it.pack_options, key=lambda p: p.minutes)
        ]
    if "subscription_fees" in sent:
        row.subscription_fees = dict(it.subscription_fees)
    if "allowed_contract_terms" in sent:
        row.allowed_contract_terms = list(it.allowed_contract_terms)
    if "allowed_billing_frequencies" in sent:
        row.allowed_billing_frequencies = list(it.allowed_billing_frequencies)
    if "one_time_fees" in sent:
        row.one_time_fees = [f.model_dump() for f in it.one_time_fees]


async def list_products(
    session: AsyncSession, actor: Actor, include_inactive: bool = False
) -> list[Product]:
    await authorize_read(session, actor, "deal.view")
    stmt = select(Product).where(Product.org_id == actor.org_id).order_by(Product.name)
    if not include_inactive:
        stmt = stmt.where(Product.active.is_(True))
    return list((await session.execute(stmt)).scalars())


async def put_products(
    actor: Actor, request: RequestInfo, items: list[Any], reason: str | None
) -> list[Product]:
    """Create or update products. A price change never touches lines on existing deals."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(CATALOG_KEY)
        cleaned = uow.reason(CATALOG_KEY, reason)
        existing = {
            p.id: p
            for p in (
                await s.execute(select(Product).where(Product.org_id == actor.org_id))
            ).scalars()
        }
        by_name = {p.name.strip().lower(): p for p in existing.values()}
        for it in items:
            if it.floor_price_paise > it.list_price_paise:
                raise ValidationFailed(f"'{it.name}': the floor price is above the list price")
            row = existing.get(it.id) if it.id else by_name.get(it.name.strip().lower())
            if row is None:
                row = Product(org_id=actor.org_id, name=it.name.strip(), unit=it.unit,
                              list_price_paise=it.list_price_paise,
                              floor_price_paise=it.floor_price_paise)  # fmt: skip
                s.add(row)
                old = None
            else:
                old = _product_snapshot(row)
                row.name, row.unit = it.name.strip(), it.unit
                row.list_price_paise, row.floor_price_paise = (
                    it.list_price_paise,
                    it.floor_price_paise,
                )
            row.description, row.active, row.updated_at = it.description, it.active, utc_now()
            _apply_pricing(row, it)
            await s.flush()
            await uow.audit(
                CATALOG_KEY, "product", row.id, old=old, new=_product_snapshot(row), reason=cleaned
            )
    from app.core.db import get_sessionmaker

    async with get_sessionmaker()() as s2:
        return await list_products(s2, actor, include_inactive=True)


# ---------------------------------------------------------------------------- reading lines
async def lines_out(session: AsyncSession, deal: Deal) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(DealProduct, Product)
            .join(Product, Product.id == DealProduct.product_id)
            .where(DealProduct.deal_id == deal.id, DealProduct.deleted_at.is_(None))
            .order_by(DealProduct.created_at)
        )
    ).all()
    pending = (
        await session.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.target_entity_type == "deal",
                ApprovalRequest.target_entity_id == str(deal.id),
                ApprovalRequest.status == "pending",
                ApprovalRequest.type.in_((VALUE_APPROVAL, DISCOUNT_TL, DISCOUNT_ADMIN)),
            )
        )
    ).scalars()
    return {
        "lines": [
            {
                "id": dp.id,
                "product_id": dp.product_id,
                "product_name": p.name,
                "quantity": dp.quantity,
                "unit_price_paise": dp.unit_price_paise,
                "discount_pct": dp.discount_pct,
                "line_total_paise": dp.line_total_paise,
                "floor_price_paise": p.floor_price_paise,
                "discount_status": dp.discount_status,
                "pending_discount_pct": dp.pending_discount_pct,
                "awaiting_approval": dp.discount_status == "pending",
            }
            for dp, p in rows
        ],
        "value_paise": deal.value_paise,
        "pending_requests": [
            {"approval_id": str(a.id), "summary": a.payload.get("summary"), "type": a.type}
            for a in pending
        ],
    }


async def get_lines(session: AsyncSession, actor: Actor, deal_id: uuid.UUID) -> dict[str, Any]:
    await authorize_read(session, actor, "deal.view", ("deal", deal_id))
    deal = await session.get(Deal, deal_id)
    if deal is None or deal.org_id != actor.org_id or deal.deleted_at is not None:
        raise NotFound("Deal not found")
    return await lines_out(session, deal)


# ---------------------------------------------------------------------------- applying a change
async def _live_lines(s: AsyncSession, deal_id: uuid.UUID) -> list[DealProduct]:
    return list(
        (
            await s.execute(
                select(DealProduct)
                .where(DealProduct.deal_id == deal_id, DealProduct.deleted_at.is_(None))
                .with_for_update()
            )
        ).scalars()
    )


async def _recompute(uow: UnitOfWork, deal: Deal) -> int:
    s = uow.session
    await s.flush()
    total = sum(l.line_total_paise for l in await _live_lines(s, deal.id))  # noqa: E741
    old = deal.value_paise
    deal.value_paise = total
    deal.version, deal.updated_at = deal.version + 1, utc_now()
    await recheck_proofs(uow, deal)
    await uow.emit(DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))
    return old


async def _apply(
    uow: UnitOfWork,
    deal: Deal,
    op: dict[str, Any],
    *,
    discount_pct: Decimal | None,
    pending_pct: Decimal | None = None,
    approval_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Make the change. `discount_pct` is the discount that takes effect now; `pending_pct` marks
    a discount that is awaiting approval."""
    s = uow.session
    kind = op["op"]
    line: DealProduct | None = None
    if kind == "add":
        product = await s.get(Product, uuid.UUID(op["product_id"]))
        assert product is not None
        pct = discount_pct or Decimal(0)
        line = DealProduct(
            org_id=deal.org_id,
            deal_id=deal.id,
            product_id=product.id,
            quantity=int(op["quantity"]),
            unit_price_paise=product.list_price_paise,
            discount_pct=pct,
            line_total_paise=line_total(int(op["quantity"]), product.list_price_paise, pct),
            created_by=uow.actor.id,
        )
        s.add(line)
    else:
        line = await s.get(DealProduct, uuid.UUID(op["line_id"]))
        if line is None or line.deleted_at is not None or line.deal_id != deal.id:
            raise NotFound("That line is gone")
        if kind == "remove":
            line.deleted_at = utc_now()
        else:
            if op.get("quantity") is not None:
                line.quantity = int(op["quantity"])
            if discount_pct is not None:
                line.discount_pct = discount_pct
            line.line_total_paise = line_total(
                line.quantity, line.unit_price_paise, line.discount_pct
            )
            line.updated_at = utc_now()
    if kind != "remove" and line is not None:
        if pending_pct is not None:
            line.discount_status, line.pending_discount_pct = "pending", pending_pct
            line.approval_id = approval_id
        elif line.discount_status == "pending" and approval_id is None and op.get("clear_pending"):
            line.discount_status, line.pending_discount_pct, line.approval_id = "none", None, None
        elif discount_pct is not None and discount_pct > 0 and line.discount_status != "pending":
            line.discount_status = "approved"
    await s.flush()
    return line.id if line else None


async def _decide_gates(
    uow: UnitOfWork,
    deal: Deal,
    *,
    base_value: int,
    old_value: int,
    discount_key_: str | None,
) -> tuple[Decision | None, Decision | None, str | None]:
    vkey = value_key(old_value, base_value)
    entity = ("deal", deal.id)
    dd = (
        await uow.authorize(discount_key_, entity=entity, owner_id=deal.owner_id)
        if discount_key_
        else None
    )
    dv = await uow.authorize(vkey, entity=entity, owner_id=deal.owner_id) if vkey else None
    return dd, dv, vkey


def _route(*decisions: Decision | None) -> str:
    routes = [d.rule.routed_to for d in decisions if d and d.effect in NEEDS_APPROVAL and d.rule]
    if ROUTE_ADMIN_POOL in routes or not routes:
        return ROUTE_ADMIN_POOL
    return routes[0] or ROUTE_ADMIN_POOL


async def change_line(
    actor: Actor,
    request: RequestInfo,
    deal_id: uuid.UUID,
    *,
    op: str,
    line_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    quantity: int | None = None,
    discount_pct: Decimal | None = None,
    reason: str | None = None,
) -> tuple[uuid.UUID, ApprovalRequest | None]:
    """add / update / remove a line. Returns (deal id, the approval request if one was needed)."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        entity = ("deal", deal.id)
        await uow.authorize("deal.edit", entity=entity, owner_id=deal.owner_id)
        if deal.status != "open":
            raise Conflict("Product lines can only be changed on open deals")
        lines = await _live_lines(s, deal.id)
        by_id = {l.id: l for l in lines}  # noqa: E741
        product: Product | None = None
        cur: DealProduct | None = None
        if op == "add":
            product = await s.get(Product, product_id) if product_id else None
            if product is None or product.org_id != deal.org_id or not product.active:
                raise ValidationFailed("Choose a product from the catalog", {"field": "product_id"})
            if quantity is None:
                raise ValidationFailed("Enter a quantity", {"field": "quantity"})
        else:
            cur = by_id.get(line_id) if line_id else None
            if cur is None:
                raise NotFound("Line not found")
            product = await s.get(Product, cur.product_id)
        assert product is not None
        floor = product.floor_price_paise
        unit = cur.unit_price_paise if cur else product.list_price_paise
        cur_disc = cur.discount_pct if cur else Decimal(0)
        want_disc = (
            (discount_pct if discount_pct is not None else cur_disc) if op != "remove" else cur_disc
        )
        qty = quantity if quantity is not None else (cur.quantity if cur else 1)
        increases = op != "remove" and want_disc > cur_disc
        dkey = discount_key(want_disc, unit, floor) if increases else None

        # the value if only the part that needs no discount approval were applied
        def total_with(disc_for_line: Decimal) -> int:
            others = sum(l.line_total_paise for l in lines if cur is None or l.id != cur.id)  # noqa: E741
            if op == "remove":
                return others
            return others + line_total(qty, unit, disc_for_line)

        old_value = deal.value_paise
        discount_needs, value_needs = False, False
        dd, dv, vkey = await _decide_gates(
            uow, deal, base_value=total_with(want_disc), old_value=old_value, discount_key_=dkey
        )
        discount_needs = bool(dd and dd.effect in NEEDS_APPROVAL)
        # if the discount must be approved, the value rule is judged on the part we apply now
        if discount_needs:
            dv_now_key = value_key(old_value, total_with(cur_disc))
            dv = (
                await uow.authorize(dv_now_key, entity=entity, owner_id=deal.owner_id)
                if dv_now_key
                else None
            )
            vkey = dv_now_key
        value_needs = bool(dv and dv.effect in NEEDS_APPROVAL)
        # reasons: the strictest of the keys involved
        cleaned = None
        for k in filter(None, (dkey, vkey)):
            cleaned = uow.reason(k, reason) or cleaned
        name = f"{product.name} for {await company_name(s, deal)}"
        op_payload: dict[str, Any] = {
            "op": op,
            "line_id": str(cur.id) if cur else None,
            "product_id": str(product.id),
            "quantity": qty,
            "discount_pct": str(want_disc),
        }
        old = {"value": format_rupees(old_value)}

        if value_needs:  # nothing is applied; the whole intended change waits for the approver
            req = await approvals.create_request(
                uow,
                type_=VALUE_APPROVAL,
                action_key=vkey or "deal.value.above_10",
                summary=f"{actor.name} asks to change the value of {company_name_text(name)}: "
                f"{op} {qty} x {product.name}"
                + (f" at {want_disc}% discount" if want_disc else ""),
                payload={"deal_id": str(deal.id), **op_payload},
                reason=reason,
                routed_to=_route(dv, dd),
                team_id=deal.team_id,
                entity=("deal", str(deal.id)),
                record_owner_id=deal.owner_id,
            )
            return deal.id, req

        if discount_needs:
            if cur is not None and cur.discount_status == "pending" and cur.approval_id:
                older = await s.get(ApprovalRequest, cur.approval_id, with_for_update=True)
                if older is not None and older.status == "pending":
                    # the newer request replaces the older one; two cannot wait on one line
                    older.status, older.decided_at = "cancelled", utc_now()
                    older.decision_reason = "Replaced by a newer discount request"
            line_id_now = await _apply(uow, deal, op_payload, discount_pct=cur_disc)
            line = await s.get(DealProduct, line_id_now) if line_id_now else None
            req = await approvals.create_request(
                uow,
                type_=dkey or DISCOUNT_ADMIN,
                action_key=dkey or DISCOUNT_ADMIN,
                summary=f"{actor.name} asks for a {want_disc}% discount on {product.name} "
                f"for {await company_name(s, deal)}",
                payload={
                    "deal_id": str(deal.id),
                    "line_id": str(line_id_now),
                    "discount_pct": str(want_disc),
                },
                reason=reason,
                routed_to=_route(dd),
                team_id=deal.team_id,
                entity=("deal", str(deal.id)),
                record_owner_id=deal.owner_id,
            )
            if line is not None:
                line.discount_status, line.pending_discount_pct = "pending", want_disc
                line.approval_id = req.id
            prev = await _recompute(uow, deal)
            await uow.audit(
                vkey or "deal.edit",
                "deal",
                deal.id,
                old={**old, "value_paise": prev},
                new={"value_paise": deal.value_paise, "discount_awaiting": str(want_disc)},
                reason=cleaned,
            )
            return deal.id, req

        await _apply(uow, deal, op_payload, discount_pct=want_disc)
        prev = await _recompute(uow, deal)
        await uow.audit(
            dkey or vkey or "deal.edit",
            "deal",
            deal.id,
            old={"value_paise": prev},
            new={"value_paise": deal.value_paise, "line": f"{op} {product.name} x{qty}",
                 "discount_pct": str(want_disc)},
            reason=cleaned,
        )  # fmt: skip
        return deal.id, None


def company_name_text(n: str) -> str:
    return n


# ---------------------------------------------------------------------------- approval executors
async def _exec_value_change(uow: UnitOfWork, req: ApprovalRequest) -> None:
    p = req.payload
    deal = await load_deal(uow, uuid.UUID(p["deal_id"]), lock=True)
    if deal.status != "open":
        raise Conflict("The deal is no longer open")
    await _apply(uow, deal, p, discount_pct=Decimal(p["discount_pct"]))
    prev = await _recompute(uow, deal)
    await uow.audit(
        req.action_key,
        "deal",
        deal.id,
        old={"value_paise": prev},
        new={"value_paise": deal.value_paise, "line": f"{p['op']} x{p['quantity']}"},
        note="Approved value change",
    )


async def _exec_discount(uow: UnitOfWork, req: ApprovalRequest) -> None:
    p = req.payload
    deal = await load_deal(uow, uuid.UUID(p["deal_id"]), lock=True)
    line = await uow.session.get(DealProduct, uuid.UUID(p["line_id"]))
    if (
        line is None
        or line.deleted_at is not None
        or line.discount_status != "pending"
        or line.approval_id != req.id
    ):
        raise Conflict("That discount request is no longer current")
    pct = Decimal(p["discount_pct"])
    line.discount_pct, line.discount_status = pct, "approved"
    line.pending_discount_pct, line.approval_id = None, None
    line.line_total_paise = line_total(line.quantity, line.unit_price_paise, pct)
    line.updated_at = utc_now()
    prev = await _recompute(uow, deal)
    await uow.audit(
        req.action_key,
        "deal",
        deal.id,
        old={"value_paise": prev},
        new={"value_paise": deal.value_paise, "discount_pct": str(pct)},
        note="Approved discount",
    )


async def _close_discount(uow: UnitOfWork, req: ApprovalRequest) -> None:
    line = await uow.session.get(DealProduct, uuid.UUID(req.payload["line_id"]))
    if line is not None and line.discount_status == "pending":
        line.discount_status, line.pending_discount_pct, line.approval_id = "none", None, None


def register() -> None:
    approvals.register_type(VALUE_APPROVAL, _exec_value_change)
    approvals.register_type(DISCOUNT_TL, _exec_discount, _close_discount)
    approvals.register_type(DISCOUNT_ADMIN, _exec_discount, _close_discount)
