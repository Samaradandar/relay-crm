# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 7: notifications (in-app and e-mail)."""

import uuid

import pytest
from sqlalchemy import select, text

from app.core.db import get_sessionmaker
from app.platform import registry
from app.platform.notifications import service as notifications
from app.platform.notifications.models import Notification
from app.platform.notifications.service import REQUIRED_TYPES
from tests.deep.phase1.kit import h, new_email
from tests.deep.phase1.test_03_isolation import make_request
from tests.helpers import dispatch_all, notifications_for

registry.setup()


async def set_pref(engine, user_id, type_, in_app, email):
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO notification_settings (user_id,type,in_app,email) VALUES (:u,:t,:a,:e) ON CONFLICT (user_id,type) DO UPDATE SET in_app=:a, email=:e"
            ),
            {"u": user_id, "t": type_, "a": in_app, "e": email},
        )


async def notify(world, user_ids, type_="lead.assigned", key=None, org=None):
    async with get_sessionmaker()() as s, s.begin():
        return await notifications.notify(
            s, org or world.org_id, user_ids, type_, "Title", "Body", "/x", dedupe_key=key
        )


async def mails(org_id) -> int:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                text("SELECT count(*) FROM outbox_events WHERE org_id=:o AND type='email.send'"),
                {"o": org_id},
            )
        ).scalar_one()


def test_required_types_are_exactly_the_documented_set():
    # P5-08: money alerts and churn risk joined the original six (approvals + user.*) as types
    # that cannot be switched off, per the Phase 5 notification-settings screen's spec.
    assert REQUIRED_TYPES == {
        "approval.requested",
        "approval.decided",
        "approval.reminder",
        "user.invited",
        "user.team_changed",
        "user.role_changed",
        "billing.payment_due",
        "billing.payment_overdue",
        "billing.low_balance",
        "billing.run_out",
        "billing.overage",
        "churn.risk",
    }


@pytest.mark.parametrize("type_", sorted(REQUIRED_TYPES))
async def test_required_types_ignore_every_switch(world, admin_engine, type_):
    await set_pref(admin_engine, world.north1.id, type_, False, False)
    assert await notify(world, [world.north1.id], type_) == 1
    got = [n for n in await notifications_for(world.north1.id) if n.type == type_]
    assert len(got) == 1
    assert await mails(world.org_id) == 1


async def test_optional_types_obey_each_channel_switch_per_user(world, admin_engine):
    ty = "lead.assigned"
    await set_pref(admin_engine, world.north1.id, ty, False, True)  # in-app off, email on
    await set_pref(admin_engine, world.north2.id, ty, True, False)  # in-app on, email off
    await set_pref(admin_engine, world.south1.id, ty, False, False)  # both off
    # world.south2 has no row: defaults are both on
    await notify(world, [world.north1.id, world.north2.id, world.south1.id, world.south2.id], ty)
    assert [
        len([n for n in await notifications_for(u.id) if n.type == ty])
        for u in (world.north1, world.north2, world.south1, world.south2)
    ] == [0, 1, 0, 1]
    async with get_sessionmaker()() as s:
        to = (
            (
                await s.execute(
                    text(
                        "SELECT payload->>'to' FROM outbox_events WHERE org_id=:o AND type='email.send'"
                    ),
                    {"o": world.org_id},
                )
            )
            .scalars()
            .all()
        )
    assert sorted(to) == sorted([world.north1.email, world.south2.email])
    # one person's setting never changes another's
    await set_pref(admin_engine, world.north1.id, ty, True, True)
    await notify(world, [world.north2.id], ty, key="k2")
    assert len([n for n in await notifications_for(world.north2.id) if n.type == ty]) == 2


async def test_inactive_deleted_and_foreign_org_users_receive_nothing(world, world_b, admin_engine):
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE users SET is_active=false WHERE id=:i"), {"i": world.north2.id}
        )
    n = await notify(
        world, [world.north2.id, world_b.north1.id, uuid.uuid4()], "approval.requested"
    )
    assert n == 0
    assert (
        await notifications_for(world.north2.id) == []
        and await notifications_for(world_b.north1.id) == []
    )
    assert await mails(world.org_id) == 0 and await mails(world_b.org_id) == 0
    # the foreign user is skipped even if org B is the notifying org A... and vice versa
    assert await notify(world_b, [world.north1.id], "approval.requested", org=world_b.org_id) == 0


async def test_dedupe_key_prevents_duplicates_for_in_app_and_email(world):
    for _ in range(4):
        await notify(world, [world.north1.id], "approval.reminder", key="same")
    assert len(await notifications_for(world.north1.id)) == 1
    assert await mails(world.org_id) == 1
    # a different user with the same key still gets one; no key means no dedupe
    await notify(world, [world.north2.id], "approval.reminder", key="same")
    assert len(await notifications_for(world.north2.id)) == 1
    await notify(world, [world.north2.id], "approval.reminder")
    await notify(world, [world.north2.id], "approval.reminder")
    assert len(await notifications_for(world.north2.id)) == 3
    # the same user listed twice in one call gets one
    await notify(world, [world.south1.id, world.south1.id], "approval.reminder", key="dup")
    assert len(await notifications_for(world.south1.id)) == 1


async def test_dedupe_key_is_scoped_by_user_not_by_type(world):
    """NOTE: the unique index is (user_id, dedupe_key): two different types that happen to share a key
    suppress each other. All built-in keys start with their type, so it is harmless today."""
    await notify(world, [world.north1.id], "approval.reminder", key="shared")
    await notify(world, [world.north1.id], "user.invited", key="shared")
    assert len(await notifications_for(world.north1.id)) == 1


async def test_email_is_queued_once_when_only_the_email_channel_is_on(world, admin_engine):
    await set_pref(admin_engine, world.north1.id, "lead.assigned", False, True)
    for _ in range(3):  # an outbox retry runs the same handler again with the same dedupe key
        await notify(world, [world.north1.id], "lead.assigned", key="once")
    assert await mails(world.org_id) == 1


async def test_nothing_queued_when_both_channels_are_off(world, admin_engine):
    await set_pref(admin_engine, world.north1.id, "lead.assigned", False, False)
    await notify(world, [world.north1.id], "lead.assigned", key="x")
    assert await notifications_for(world.north1.id) == [] and await mails(world.org_id) == 0


# ------------------------------------------------------------------ the bell over HTTP
async def test_mark_read_only_touches_the_callers_own_rows(client, world):
    await notify(
        world, [world.north1.id, world.north2.id, world.admin.id], "approval.reminder", key="a"
    )
    mine = (await client.get("/notifications", headers=h(world.north1))).json()
    theirs = (await client.get("/notifications", headers=h(world.north2))).json()
    assert mine["unread"] == 1 and theirs["unread"] == 1
    r = await client.patch(
        "/notifications", json={"ids": [theirs["items"][0]["id"]]}, headers=h(world.north1)
    )
    assert r.json() == {"marked": 0}
    assert (await client.get("/notifications", headers=h(world.north2))).json()["unread"] == 1
    # mix of own and foreign ids marks only the own
    r = await client.patch(
        "/notifications",
        json={"ids": [theirs["items"][0]["id"], mine["items"][0]["id"], str(uuid.uuid4())]},
        headers=h(world.north1),
    )
    assert r.json() == {"marked": 1}
    # idempotent
    r = await client.patch(
        "/notifications", json={"ids": [mine["items"][0]["id"]]}, headers=h(world.north1)
    )
    assert r.json() == {"marked": 0}
    # null ids marks all of the caller's, no one else's
    await notify(world, [world.north1.id, world.north2.id], "approval.reminder", key="b")
    await notify(world, [world.north1.id], "approval.reminder", key="c")
    r = await client.patch("/notifications", json={}, headers=h(world.north1))
    assert r.json() == {"marked": 2}
    assert (await client.get("/notifications", headers=h(world.north2))).json()["unread"] == 2
    assert (await client.get("/notifications", headers=h(world.admin))).json()["unread"] == 1


async def test_list_filters_ordering_and_shape(client, world):
    for i in range(3):
        await notify(world, [world.north1.id], "approval.reminder", key=f"o{i}")
    body = (await client.get("/notifications", headers=h(world.north1))).json()
    ts = [i["created_at"] for i in body["items"]]
    assert ts == sorted(ts, reverse=True) and body["unread"] == 3
    first = body["items"][0]
    assert set(first) == {"id", "type", "title", "body", "link", "read_at", "created_at"}
    await client.patch("/notifications", json={"ids": [first["id"]]}, headers=h(world.north1))
    assert (
        len(
            (await client.get("/notifications?unread_only=true", headers=h(world.north1))).json()[
                "items"
            ]
        )
        == 2
    )
    assert len((await client.get("/notifications", headers=h(world.north1))).json()["items"]) == 3
    assert (
        await client.get("/notifications?unread_only=maybe", headers=h(world.north1))
    ).status_code == 422


async def test_unread_count_is_not_capped_at_the_page_size(client, world, admin_engine):
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO notifications (org_id,user_id,type,title,body) SELECT :o,:u,'x','t','b' FROM generate_series(1,60)"
            ),
            {"o": world.org_id, "u": world.north1.id},
        )
    body = (await client.get("/notifications", headers=h(world.north1))).json()
    assert body["unread"] == 60


@pytest.mark.parametrize(
    "body", [{"ids": "x"}, {"ids": ["not-a-uuid"]}, {"ids": [1]}, {"ids": {}}, [], "x"]
)
async def test_mark_read_bad_bodies_are_422(client, world, body):
    assert (
        await client.patch("/notifications", json=body, headers=h(world.north1))
    ).status_code == 422


# ------------------------------------------------------------------ built-in notifications from real flows
async def test_user_flows_notify_the_right_people_exactly_once(client, world):
    A = h(world.admin)
    r = await client.post(
        "/users",
        json={
            "name": "Flow",
            "email": new_email(),
            "role": "user",
            "team_id": str(world.north_team),
            "reason": "hire",
        },
        headers=A,
    )
    uid = uuid.UUID(r.json()["id"])
    await dispatch_all()
    assert [n.type for n in await notifications_for(uid)] == ["user.invited"]
    await client.patch(
        f"/users/{uid}", json={"team_id": str(world.south_team), "reason": "move"}, headers=A
    )
    await client.patch(
        f"/users/{uid}", json={"role": "team_leader", "reason": "x"}, headers=A
    )  # south has a leader -> 409
    await client.patch(f"/users/{uid}", json={"name": "Flow 2", "reason": "rename"}, headers=A)
    await dispatch_all()
    await dispatch_all()
    types = [n.type for n in await notifications_for(uid)]
    assert (
        types.count("user.team_changed") == 1
        and types.count("user.invited") == 1
        and "user.role_changed" not in types
    )
    # role change notifies
    await client.patch(
        f"/users/{world.tl_south.id}", json={"role": "user", "reason": "step down"}, headers=A
    )
    await dispatch_all()
    assert [n.type for n in await notifications_for(world.tl_south.id)] == ["user.role_changed"]
    # deactivated users get nothing about their own deactivation (and cannot read it anyway)
    await client.patch(
        f"/users/{world.south2.id}", json={"is_active": False, "reason": "left"}, headers=A
    )
    await dispatch_all()
    assert await notifications_for(world.south2.id) == []
    # the requester is never told to approve their own request
    await make_request(world.tl_north)
    await dispatch_all()
    assert [n.type for n in await notifications_for(world.tl_north.id)] == []
    got = {u.name: [n.type for n in await notifications_for(u.id)] for u in (world.admin, world.sa)}
    assert got == {"Anita": ["approval.requested"], "Samar": ["approval.requested"]}


async def test_each_notification_queues_exactly_one_email_event(world):
    await make_request(world.tl_north)
    await make_request(world.north1, routed_to="team_leader", team_id=world.north_team)
    await dispatch_all()
    async with get_sessionmaker()() as s:
        notes = (
            (await s.execute(select(Notification).where(Notification.org_id == world.org_id)))
            .scalars()
            .all()
        )
        n_mail = (
            await s.execute(
                text("SELECT count(*) FROM outbox_events WHERE org_id=:o AND type='email.send'"),
                {"o": world.org_id},
            )
        ).scalar_one()
    assert len(notes) == n_mail == 5  # request 1: admin, SA; request 2: admin, SA, TL
    await dispatch_all()
    assert len(await notifications_for(world.admin.id)) == 2
