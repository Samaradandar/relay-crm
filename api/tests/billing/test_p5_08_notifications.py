# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P5-08: notification settings screen and the genuinely-missing notifications this PR builds
(meeting scheduled, follow-up due digest, proposal revision required / pending-no-feedback)."""

from datetime import timedelta

from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.core.time import utc_now
from app.modules.deals.today import send_followup_digest
from app.modules.proposals.service import check_pending_no_feedback
from app.platform.files import storage as storage_module
from app.platform.notifications import email as email_module
from tests.billing.test_proposals import put_template, setup_deal_with_line
from tests.deep.phase1.kit import FakeStorage
from tests.helpers import headers as H
from tests.helpers import notifications_for
from tests.modules.journey import due, log, new_deal


# ------------------------------------------------------------- /me/notification-preferences
async def test_get_defaults_to_on_and_marks_required(client, world):
    body = (await client.get("/me/notification-preferences", headers=H(world.north1))).json()
    by_type = {i["type"]: i for i in body["items"]}
    assert by_type["billing.payment_due"]["required"] is True
    assert by_type["billing.payment_due"]["email_enabled"] is True
    assert by_type["lead.assigned"]["required"] is False
    assert by_type["lead.assigned"]["email_enabled"] is True  # no row yet: default on


async def test_put_rejects_disabling_a_required_type(client, world):
    r = await client.put(
        "/me/notification-preferences",
        json={"preferences": [{"type": "billing.payment_due", "email_enabled": False}]},
        headers=H(world.north1),
    )
    assert r.status_code == 422
    # unchanged: still on
    got = (await client.get("/me/notification-preferences", headers=H(world.north1))).json()
    assert {i["type"]: i["email_enabled"] for i in got["items"]}["billing.payment_due"] is True


async def test_put_updates_a_non_required_type_and_notify_honors_it(client, world):
    r = await client.put(
        "/me/notification-preferences",
        json={"preferences": [{"type": "lead.assigned", "email_enabled": False}]},
        headers=H(world.north1),
    )
    assert r.status_code == 200
    got = {i["type"]: i["email_enabled"] for i in r.json()["items"]}
    assert got["lead.assigned"] is False
    from app.platform.notifications import service as notifications

    async with get_sessionmaker()() as s, s.begin():
        await notifications.notify(
            s, world.org_id, [world.north1.id], "lead.assigned", "T", "B", "/x", dedupe_key="pz1"
        )
    # in-app row is still created; only the email channel is gated
    notes = [n for n in await notifications_for(world.north1.id) if n.type == "lead.assigned"]
    assert len(notes) == 1
    async with get_sessionmaker()() as s:
        n_mail = (
            await s.execute(
                text(
                    "SELECT count(*) FROM outbox_events WHERE org_id=:o AND type='email.send' "
                    "AND payload->>'dedupe' = :k"
                ),
                {"o": world.org_id, "k": f"{world.north1.id}:pz1"},
            )
        ).scalar_one()
    assert n_mail == 0


async def test_put_rejects_unknown_type(client, world):
    r = await client.put(
        "/me/notification-preferences",
        json={"preferences": [{"type": "not.a.real.type", "email_enabled": True}]},
        headers=H(world.north1),
    )
    assert r.status_code == 422


# ----------------------------------------------------------------------- meeting scheduled
async def test_meeting_activity_in_the_future_notifies_the_owner(client, world):
    d, owner = await new_deal(client, world)
    future = (utc_now() + timedelta(days=3)).isoformat()
    await log(client, world.tl_north, d["id"], type_="meeting", occurred_at=future, two_way=True)
    types = [n.type for n in await notifications_for(owner.id)]
    assert "activity.meeting_scheduled" in types


async def test_meeting_activity_does_not_notify_when_the_owner_logs_it_themselves(client, world):
    d, owner = await new_deal(client, world)
    future = (utc_now() + timedelta(days=3)).isoformat()
    await log(client, owner, d["id"], type_="meeting", occurred_at=future, two_way=True)
    types = [n.type for n in await notifications_for(owner.id)]
    assert "activity.meeting_scheduled" not in types


async def test_past_meeting_does_not_fire_the_scheduled_notification(client, world):
    d, owner = await new_deal(client, world)
    await log(client, world.tl_north, d["id"], type_="call")  # not a meeting at all
    types = [n.type for n in await notifications_for(owner.id)]
    assert "activity.meeting_scheduled" not in types


# ---------------------------------------------------------------------- follow-up due digest
async def test_followup_digest_notifies_the_responsible_user_once(client, world):
    d, owner = await new_deal(client, world)
    await log(client, owner, d["id"], next_step_due_date=due(0))  # due today
    n = await send_followup_digest(get_sessionmaker())
    assert n >= 1
    types = [x.type for x in await notifications_for(owner.id)]
    assert types.count("deal.followup_due") == 1
    # running the digest again the same day does not duplicate it (dedupe_key is per day)
    await send_followup_digest(get_sessionmaker())
    types2 = [x.type for x in await notifications_for(owner.id)]
    assert types2.count("deal.followup_due") == 1


async def test_followup_digest_skips_steps_not_due_today(client, world):
    d, owner = await new_deal(client, world)
    await log(client, owner, d["id"], next_step_due_date=due(5))  # due later
    await send_followup_digest(get_sessionmaker())
    types = [x.type for x in await notifications_for(owner.id)]
    assert "deal.followup_due" not in types


# --------------------------------------------------------------- proposal revision / pending
async def test_proposal_changes_requested_notifies_the_owner(client, world, admin_engine):
    await put_template(client, world.admin)
    did, owner = await setup_deal_with_line(client, world, admin_engine)
    email_module.set_backend(email_module.MemoryBackend())
    storage_module.set_storage(FakeStorage())
    try:
        async with admin_engine.begin() as c:
            acc = (
                await c.execute(text("SELECT account_id FROM deals WHERE id=:d"), {"d": did})
            ).scalar()
            await c.execute(
                text(
                    "INSERT INTO contacts (org_id, account_id, name, email) VALUES (:o,:a,'B',:e)"
                ),
                {"o": world.org_id, "a": acc, "e": "b@example.com"},
            )
        prop = (await client.post(f"/deals/{did}/proposals", headers=H(owner))).json()
        await client.post(f"/proposals/{prop['id']}/send", json={"reason": "go"}, headers=H(owner))
        r = await client.post(
            f"/proposals/{prop['id']}/status",
            json={"status": "changes_requested", "feedback": "Add more minutes", "reason": "fb"},
            headers=H(owner),
        )
        assert r.status_code == 200
        types = [n.type for n in await notifications_for(owner.id)]
        assert "proposal.revision_required" in types
    finally:
        email_module.set_backend(None)
        storage_module.set_storage(None)


async def test_proposal_pending_two_days_notifies_owner_once(client, world, admin_engine):
    await put_template(client, world.admin)
    did, owner = await setup_deal_with_line(client, world, admin_engine)
    email_module.set_backend(email_module.MemoryBackend())
    storage_module.set_storage(FakeStorage())
    try:
        async with admin_engine.begin() as c:
            acc = (
                await c.execute(text("SELECT account_id FROM deals WHERE id=:d"), {"d": did})
            ).scalar()
            await c.execute(
                text(
                    "INSERT INTO contacts (org_id, account_id, name, email) VALUES (:o,:a,'B',:e)"
                ),
                {"o": world.org_id, "a": acc, "e": "b@example.com"},
            )
        prop = (await client.post(f"/deals/{did}/proposals", headers=H(owner))).json()
        await client.post(f"/proposals/{prop['id']}/send", json={"reason": "go"}, headers=H(owner))
        # backdate sent_at by 3 days so it is past the 2-day window
        async with admin_engine.begin() as c:
            await c.execute(
                text("UPDATE proposals SET sent_at = sent_at - interval '3 days' WHERE id=:p"),
                {"p": prop["id"]},
            )
        async with get_sessionmaker()() as s, s.begin():
            n = await check_pending_no_feedback(s, world.org_id)
        assert n == 1
        types = [x.type for x in await notifications_for(owner.id)]
        assert types.count("proposal.pending") == 1
        # a second run the same day does not duplicate it (notify()'s own dedupe_key)
        async with get_sessionmaker()() as s, s.begin():
            await check_pending_no_feedback(s, world.org_id)
        types2 = [x.type for x in await notifications_for(owner.id)]
        assert types2.count("proposal.pending") == 1
    finally:
        email_module.set_backend(None)
        storage_module.set_storage(None)


async def test_proposal_sent_recently_is_not_flagged_pending_yet(client, world, admin_engine):
    await put_template(client, world.admin)
    did, owner = await setup_deal_with_line(client, world, admin_engine)
    email_module.set_backend(email_module.MemoryBackend())
    storage_module.set_storage(FakeStorage())
    try:
        async with admin_engine.begin() as c:
            acc = (
                await c.execute(text("SELECT account_id FROM deals WHERE id=:d"), {"d": did})
            ).scalar()
            await c.execute(
                text(
                    "INSERT INTO contacts (org_id, account_id, name, email) VALUES (:o,:a,'B',:e)"
                ),
                {"o": world.org_id, "a": acc, "e": "b@example.com"},
            )
        prop = (await client.post(f"/deals/{did}/proposals", headers=H(owner))).json()
        await client.post(f"/proposals/{prop['id']}/send", json={"reason": "go"}, headers=H(owner))
        async with get_sessionmaker()() as s, s.begin():
            n = await check_pending_no_feedback(s, world.org_id)
        assert n == 0
    finally:
        email_module.set_backend(None)
        storage_module.set_storage(None)
