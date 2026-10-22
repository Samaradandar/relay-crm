# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Small helpers shared by the API and platform tests."""

from typing import Any

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.security import DevAuthProvider, get_auth_provider
from app.modules.users.models import Team, User
from app.platform.context import Actor
from app.platform.notifications.models import Notification
from app.platform.outbox.service import dispatch_batch


def headers(actor: Actor) -> dict[str, str]:
    provider = get_auth_provider()
    assert isinstance(provider, DevAuthProvider)
    assert actor.email
    return {"Authorization": f"Bearer {provider.issue(actor.email)}"}


async def dispatch_all() -> int:
    """Run the outbox dispatcher until nothing is due (what the worker does continuously)."""
    total = 0
    while True:
        n = await dispatch_batch(get_sessionmaker())
        if n == 0:
            return total
        total += n


async def notifications_for(user_id: Any) -> list[Notification]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(Notification)
                    .where(Notification.user_id == user_id)
                    .order_by(Notification.created_at)
                )
            ).scalars()
        )


async def get_user_row(user_id: Any) -> User:
    async with get_sessionmaker()() as s:
        return (await s.execute(select(User).where(User.id == user_id))).scalar_one()


async def _actor_row(user_id: Any) -> Actor:
    async with get_sessionmaker()() as s:
        u = (await s.execute(select(User).where(User.id == user_id))).scalar_one()
    return Actor(
        id=u.id, org_id=u.org_id, role=u.role, team_id=u.team_id, name=u.name, email=u.email
    )


async def settle_rep_lead(client: Any, deal: dict[str, Any]) -> dict[str, Any]:
    """A lead a sales rep creates now waits for the Team Leader (by design). Tests that
    only need 'a lead with an owner' call this: the routed Team Leader chooses automatic round
    robin, exactly what a rep's lead used to get at once. Returns the deal as the Admin sees it.
    Leads that are not held are returned unchanged."""
    if not deal.get("awaiting_team_leader"):
        return deal
    async with get_sessionmaker()() as s:
        leader = (
            await s.execute(select(Team.leader_id).where(Team.id == deal["team_id"]))
        ).scalar_one()
        org = (await s.execute(select(User.org_id).where(User.id == leader))).scalar_one()
        admin = (
            (await s.execute(select(User.id).where(User.org_id == org, User.role == "admin")))
            .scalars()
            .first()
        )
    tl = await _actor_row(leader)
    r = await client.post(
        f"/pool/{deal['id']}/decide", json={"action": "auto"}, headers=headers(tl)
    )
    assert r.status_code == 200, r.text
    got = await client.get(f"/deals/{deal['id']}", headers=headers(await _actor_row(admin)))
    assert got.status_code == 200, got.text
    return {**got.json(), "warning": deal.get("warning")}
