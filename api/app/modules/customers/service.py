# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Customers: one per account once it has a Won deal; the winning rep keeps the account (P4-01).

A customer follows `customer.view` scope: User own customers, Team Leader their team, Admin and
Super admin all. Creation runs from the `deal.won` outbox event, which is delivered at least
once, so it is idempotent: a second delivery (or a second Won deal on the same account) creates
nothing and changes no owner.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.time import utc_now
from app.modules.accounts.models import Account
from app.modules.customers import events
from app.modules.customers.models import Customer
from app.modules.leads.models import Deal
from app.modules.pipeline.engine import events as pipeline_events
from app.modules.users.models import User
from app.platform.audit import service as audit_service
from app.platform.context import Actor, RequestInfo, system_actor
from app.platform.outbox import service as outbox
from app.platform.outbox.models import OutboxEvent
from app.platform.outbox.service import on_event
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

VIEW_KEY = "customer.view"
CREATE_KEY = "customer.create"


async def _by_account(session: AsyncSession, account_id: uuid.UUID) -> Customer | None:
    return (
        await session.execute(select(Customer).where(Customer.account_id == account_id))
    ).scalar_one_or_none()


async def ensure_customer(
    session: AsyncSession, deal: Deal, fallback_owner: uuid.UUID | None = None
) -> tuple[Customer, bool]:
    """The customer for the deal's account, created if the account has none. Returns
    (customer, created). The winning rep (the deal owner, else whoever moved it) keeps the
    account: the customer and the account both carry them as owner."""
    existing = await _by_account(session, deal.account_id)
    if existing is not None:
        return existing, False
    owner_id = deal.owner_id or fallback_owner
    if owner_id is None:
        raise RuntimeError(f"deal {deal.id} was won with no owner, so it has no account owner")
    inserted = (
        await session.execute(
            pg_insert(Customer)
            .values(
                org_id=deal.org_id,
                account_id=deal.account_id,
                account_owner_id=owner_id,
                first_won_deal_id=deal.id,
            )
            .on_conflict_do_nothing(index_elements=[Customer.account_id])
            .returning(Customer.id)
        )
    ).scalar_one_or_none()
    if inserted is None:  # another delivery created it between our read and insert
        again = await _by_account(session, deal.account_id)
        assert again is not None
        return again, False
    customer = await session.get(Customer, inserted, populate_existing=True)
    assert customer is not None
    account = await session.get(Account, deal.account_id)
    old_owner = account.account_owner_id if account else None
    if account is not None:
        account.account_owner_id, account.updated_at = owner_id, utc_now()
    await audit_service.append(
        session,
        system_actor(deal.org_id),
        CREATE_KEY,
        "customer",
        customer.id,
        new={
            "account_id": str(deal.account_id),
            "account_owner_id": str(owner_id),
            "previous_account_owner_id": str(old_owner) if old_owner else None,
            "first_won_deal_id": str(deal.id),
        },
        reason="Deal won",
        request=RequestInfo(origin="system_job"),
    )
    await outbox.enqueue(
        session,
        deal.org_id,
        events.CUSTOMER_CREATED,
        {
            "customer_id": str(customer.id),
            "account_id": str(deal.account_id),
            "owner_id": str(owner_id),
            "deal_id": str(deal.id),
        },
    )
    return customer, True


@on_event(pipeline_events.DEAL_WON)
async def on_deal_won(session: AsyncSession, event: OutboxEvent) -> None:
    payload = event.payload
    deal = await session.get(Deal, uuid.UUID(payload["deal_id"]))
    if deal is None:
        return
    by_id = payload.get("by_id")
    await ensure_customer(session, deal, uuid.UUID(by_id) if by_id else None)


# ---------------------------------------------------------------------------- reads
def _row(c: Customer, account_name: str, owner_name: str | None) -> dict[str, Any]:
    return {
        "id": c.id,
        "account_id": c.account_id,
        "account_name": account_name,
        "account_owner_id": c.account_owner_id,
        "account_owner_name": owner_name,
        "first_won_deal_id": c.first_won_deal_id,
        "status": c.status,
        "voice_platform_customer_id": c.voice_platform_customer_id,
        "created_at": c.created_at,
    }


async def _scoped(session: AsyncSession, actor: Actor, *where: Any) -> list[dict[str, Any]]:
    decision = await authorize_read(session, actor, VIEW_KEY)
    rows = await session.execute(
        select(Customer, Account.name, User.name)
        .join(Account, Account.id == Customer.account_id)
        .outerjoin(User, User.id == Customer.account_owner_id)
        .where(
            *scope_conditions(
                actor, Customer, decision.scope, owner_attr="account_owner_id", hide_sa_owned=False
            ),
            Customer.deleted_at.is_(None),
            *where,
        )
        .order_by(Account.name, Customer.id)
    )
    return [_row(c, a, o) for c, a, o in rows.all()]


async def list_customers(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    return await _scoped(session, actor)


async def get_customer(
    session: AsyncSession, actor: Actor, customer_id: uuid.UUID
) -> dict[str, Any]:
    rows = await _scoped(session, actor, Customer.id == customer_id)
    if not rows:
        raise NotFound("Customer not found")
    return rows[0]


MAPPING_KEY = "catalog.template.manage"  # Admin + reason, Super admin (same shape as the mapping)


async def set_voice_mapping(
    actor: Actor,
    request: RequestInfo,
    customer_id: uuid.UUID,
    voice_platform_customer_id: str | None,
    reason: str | None,
) -> dict[str, Any]:
    """Map a customer to its id in the voice platform's usage feed (P4-08). Admin + reason, or
    Super admin; unmapped usage goes to the unmatched queue instead of being dropped."""
    async with UnitOfWork(actor, request) as uow:
        customer = await uow.session.get(Customer, customer_id)
        if customer is None or customer.deleted_at is not None:
            raise NotFound("Customer not found")
        await uow.authorize(MAPPING_KEY, entity=("customer", customer_id))
        cleaned = uow.reason(MAPPING_KEY, reason)
        old = {"voice_platform_customer_id": customer.voice_platform_customer_id}
        customer.voice_platform_customer_id = voice_platform_customer_id
        customer.updated_at = utc_now()
        await uow.session.flush()
        await uow.audit(
            MAPPING_KEY,
            "customer",
            customer.id,
            old=old,
            new={"voice_platform_customer_id": voice_platform_customer_id},
            reason=cleaned,
        )
        rows = await _scoped(uow.session, actor, Customer.id == customer_id)
    return rows[0] if rows else _row(customer, "", None)
