# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 7 deep tests: capture_lead()'s existing-customer check, duplicate rule, idempotency,
and that it never bypasses create_lead's own pooling/assignment path.
"""

import uuid

import pytest
from sqlalchemy import select, text

from app.modules.capture.models import CaptureReview
from app.modules.capture.service import capture_lead
from app.modules.leads.models import Deal
from tests.modules.journey import run_to
from tests.modules.test_leads_api import configure, create


@pytest.fixture
async def session(database):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(database.app_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_website_capture_creates_pooled_lead_with_source(client, world, session):
    await configure(client, world)
    payload = {
        "company": "Newco Industries", "name": "Priyanka Shah", "phone": "+91 98765 00011",
        "email": "priyanka@newco-industries.in", "message": "Interested in the voice agent",
    }  # fmt: skip
    event = await capture_lead(session, world.org_id, "website", payload)
    await session.commit()
    assert event.outcome == "created"
    assert event.deal_id is not None

    deal = await session.get(Deal, event.deal_id)
    assert deal.source_kind == "website"
    assert deal.lead_source == "Website"
    assert deal.status == "pooled"  # the engine hasn't run yet (dispatched via the outbox worker)


async def test_same_submission_twice_creates_one_lead(client, world, session):
    await configure(client, world)
    payload = {"company": "Oneshot Co", "name": "Ravi Teja", "phone": "+91 98765 00022"}
    first = await capture_lead(session, world.org_id, "website", payload)
    await session.commit()
    second = await capture_lead(session, world.org_id, "website", payload)
    await session.commit()
    assert first.id == second.id
    assert first.deal_id == second.deal_id

    count = (
        await session.execute(
            select(Deal.id).where(
                Deal.account_id == (await session.get(Deal, first.deal_id)).account_id
            )
        )
    ).all()
    assert len(count) == 1


async def test_exact_duplicate_notes_the_existing_deal_and_creates_no_lead(client, world, session):
    await configure(client, world)
    existing_email = f"{uuid.uuid4().hex[:8]}@acme-exact.in"
    deal = await create(client, world.north1, company="Acme Exact Co", email=existing_email)

    event = await capture_lead(
        session, world.org_id, "email",
        {"company": "Acme Exact Co", "name": "Someone else", "email": existing_email, "message": "Again"},
    )  # fmt: skip
    await session.commit()
    assert event.outcome == "duplicate"
    assert event.deal_id is None  # no NEW lead is created

    noted = await session.get(Deal, uuid.UUID(deal["id"]))
    assert "capture_notes" in (noted.flags or {})


async def test_similar_name_only_is_held_for_review_not_created(client, world, session):
    await configure(client, world)
    await create(client, world.north1, company="Globex Corporation Pvt Ltd")

    event = await capture_lead(
        session, world.org_id, "website",
        {
            "company": "Globex Corp", "name": "Different Person",
            "phone": f"+91 9{uuid.uuid4().int % 10**9:09d}",
        },
    )  # fmt: skip
    await session.commit()
    assert event.outcome == "needs_review"
    assert event.deal_id is None

    review = (
        await session.execute(
            select(CaptureReview).where(CaptureReview.capture_event_id == event.id)
        )
    ).scalar_one()
    assert review.status == "open"


async def test_existing_customer_enquiry_never_creates_a_lead(client, world, admin_engine, session):
    deal, owner = await run_to(client, world, admin_engine, "onboarding")
    async with admin_engine.begin() as c:
        account_id = (
            await c.execute(text("SELECT account_id FROM deals WHERE id=:d"), {"d": deal["id"]})
        ).scalar_one()
        customer_id = uuid.uuid4()
        await c.execute(
            text(
                "INSERT INTO customers (id, org_id, account_id, account_owner_id, first_won_deal_id, status) "
                "VALUES (:i, :o, :a, :u, :d, 'active')"
            ),
            {"i": customer_id, "o": world.org_id, "a": account_id, "u": owner.id, "d": deal["id"]},
        )
        email = (
            await c.execute(
                text("SELECT email FROM contacts WHERE account_id=:a LIMIT 1"), {"a": account_id}
            )
        ).scalar_one_or_none()

    event = await capture_lead(
        session, world.org_id, "website",
        {"company": "Whatever name", "name": "New enquiry", "email": email, "message": "Upsell interest"},
    )  # fmt: skip
    await session.commit()
    assert event.outcome == "existing_customer"
    assert event.deal_id is None
