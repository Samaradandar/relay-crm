# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Helpers for the Phase 2 deep tests (copied or adapted from tests/modules; nothing is edited)."""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.leads.service import today_ist
from app.modules.pipeline.seeds import seed_org_pipeline
from app.platform.permissions.roles import Role
from tests.conftest import World, insert_user
from tests.helpers import dispatch_all, headers, settle_rep_lead

__all__ = [
    "World",
    "actor_by_id",
    "audit_rows",
    "configure",
    "create",
    "dispatch_all",
    "due",
    "headers",
    "invariant_violations",
    "lead",
    "make_world",
    "put_settings",
    "q",
    "q1",
]


def due(days: int = 1) -> str:
    return (today_ist() + timedelta(days=days)).isoformat()


def lead(company: str | None = None, **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "company": company if company is not None else "Co " + uuid.uuid4().hex[:12],
        "contact_name": "Asha Rao",
        "email": f"{uuid.uuid4().hex[:10]}@example.com",
        "lead_source": "Website",
        "next_step_text": "Call to qualify",
        "next_step_due_date": due(1),
    }
    body.update(kw)
    return {k: v for k, v in body.items() if v is not _DROP}


_DROP = object()
DROP = _DROP


async def put_settings(client, actor, world, expect=200, **kw):
    body = {
        "default_team_id": str(world.north_team),
        "default_cap": 25,
        "rules": [],
        "groups": [],
        "caps": {},
        "reason": "setup",
        **kw,
    }
    r = await client.put("/settings/assignment", json=body, headers=headers(actor))
    assert r.status_code == expect, r.text
    return r.json()


async def configure(client, world, cap=25, team=None, **kw):
    return await put_settings(
        client,
        world.admin,
        world,
        default_team_id=str(team or world.north_team),
        default_cap=cap,
        **kw,
    )


async def create(client, actor, expect=201, **kw):
    r = await client.post("/leads", json=lead(**kw), headers=headers(actor))
    assert r.status_code == expect, r.text
    if expect != 201:
        return r.json()
    # a rep's lead waits for the Team Leader; most tests just need it owned (round robin)
    return await settle_rep_lead(client, r.json())


async def create_via_leader(client, actor, expect=201, **kw):
    """Create the lead as the actor's Team Leader. A rep's own lead is held for the leader, but the
    engine tests are about what the ENGINE does with a lead that enters the Pool, so they drive it
    with a Team Leader (whose leads go straight to the engine, routed by rules / default team)."""
    from sqlalchemy import select

    from app.core.db import get_sessionmaker
    from app.modules.users.models import Team
    from tests.helpers import _actor_row

    leader = actor
    if actor.team_id is not None and actor.role == "user":
        async with get_sessionmaker()() as s:
            lid = (
                await s.execute(select(Team.leader_id).where(Team.id == actor.team_id))
            ).scalar_one()
        leader = await _actor_row(lid)
    r = await client.post("/leads", json=lead(**kw), headers=headers(leader))
    assert r.status_code == expect, r.text
    return r.json()


def actor_by_id(world, uid):
    return next(a for a in world.all_actors if str(a.id) == str(uid))


async def q(engine: AsyncEngine, sql: str, **params: Any) -> list[Any]:
    async with engine.connect() as c:
        return list((await c.execute(text(sql), params)).all())


async def q1(engine: AsyncEngine, sql: str, **params: Any) -> Any:
    rows = await q(engine, sql, **params)
    return rows[0][0] if rows else None


async def audit_rows(engine: AsyncEngine, entity_id: Any, org_id: Any = None) -> list[Any]:
    return await q(
        engine,
        "SELECT action_key, outcome, entity_type, reason, origin, actor_role, note FROM audit_log "
        "WHERE entity_id = :e ORDER BY seq",
        e=str(entity_id),
    )


async def make_world(engine: AsyncEngine, label: str = "Other org") -> World:
    """A second organisation with the same cast (used for cross-org isolation tests)."""
    org_id = uuid.uuid4()
    north, south = uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as c:
        await c.execute(
            text("INSERT INTO orgs (id, name) VALUES (:i, :n)"), {"i": org_id, "n": label}
        )
        await seed_org_pipeline(c, org_id)
        for tid, tname in ((north, "Team North"), (south, "Team South")):
            await c.execute(
                text("INSERT INTO teams (id, org_id, name) VALUES (:i,:o,:n)"),
                {"i": tid, "o": org_id, "n": tname},
            )
    sa = await insert_user(engine, org_id, "Samar", Role.SUPER_ADMIN, None)
    admin = await insert_user(engine, org_id, "Anita", Role.ADMIN, None)
    tl_n = await insert_user(engine, org_id, "Tara", Role.TEAM_LEADER, north)
    tl_s = await insert_user(engine, org_id, "Tejas", Role.TEAM_LEADER, south)
    users = [
        await insert_user(engine, org_id, n, Role.USER, t)
        for n, t in (("Priya", north), ("Rahul", north), ("Sneha", south), ("Vikram", south))
    ]
    async with engine.begin() as c:
        for tid, leader in ((north, tl_n), (south, tl_s)):
            await c.execute(
                text("UPDATE teams SET leader_id = :l WHERE id = :t"), {"l": leader.id, "t": tid}
            )
    return World(org_id, sa, admin, tl_n, tl_s, *users, north, south)


# --------------------------------------------------------------------------- invariants
async def invariant_violations(engine: AsyncEngine, org_id: Any) -> list[str]:
    """Raw-SQL checks of the Phase 2 guarantees. Returns a list of human readable violations."""
    bad: list[str] = []

    async def rows(sql: str, **p: Any) -> list[Any]:
        return await q(engine, sql, o=org_id, **p)

    for r in await rows(
        "SELECT d.id FROM deals d WHERE d.org_id=:o AND d.status='pooled' AND d.deleted_at IS NULL "
        "AND (SELECT count(*) FROM lead_pool_entries e WHERE e.deal_id=d.id AND e.left_at IS NULL) <> 1"
    ):
        bad.append(f"pooled deal {r[0]} does not have exactly one open pool entry")
    for r in await rows(
        "SELECT d.id FROM deals d WHERE d.org_id=:o AND d.status <> 'pooled' AND d.deleted_at IS NULL "
        "AND EXISTS (SELECT 1 FROM lead_pool_entries e WHERE e.deal_id=d.id AND e.left_at IS NULL)"
    ):
        bad.append(f"non-pooled deal {r[0]} still has an open pool entry")
    for r in await rows(
        "SELECT d.id FROM deals d WHERE d.org_id=:o AND d.deleted_at IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM lead_pool_entries e WHERE e.deal_id=d.id AND e.left_at IS NULL)"
    ):
        bad.append(f"deleted deal {r[0]} still has an open pool entry")
    for r in await rows(
        "SELECT d.id FROM deals d LEFT JOIN users u ON u.id=d.owner_id WHERE d.org_id=:o "
        "AND d.status='open' AND d.deleted_at IS NULL AND (d.owner_id IS NULL OR "
        "(d.team_id IS NULL AND u.role NOT IN ('super_admin','admin')))"
    ):
        bad.append(f"open deal {r[0]} has no owner or no team")
    for r in await rows(
        "SELECT d.id FROM deals d JOIN users u ON u.id=d.owner_id WHERE d.org_id=:o "
        "AND d.status='open' AND d.deleted_at IS NULL AND d.team_id IS DISTINCT FROM u.team_id"
    ):
        bad.append(f"open deal {r[0]} team differs from its owner's team")
    for r in await rows(
        "SELECT d.id FROM deals d WHERE d.org_id=:o AND d.status='pooled' AND d.owner_id IS NOT NULL"
    ):
        bad.append(f"pooled deal {r[0]} has an owner")
    for r in await rows(
        "SELECT d.id, count(*) FROM deals d JOIN next_steps n ON n.deal_id=d.id AND n.done_at IS NULL "
        "WHERE d.org_id=:o AND d.status IN ('open','pooled') AND d.deleted_at IS NULL "
        "GROUP BY d.id HAVING count(*) <> 1"
    ):
        bad.append(f"deal {r[0]} has {r[1]} open next steps")
    for r in await rows(
        "SELECT d.id FROM deals d JOIN next_steps n ON n.deal_id=d.id AND n.done_at IS NULL "
        "WHERE d.org_id=:o AND d.status='open' AND d.deleted_at IS NULL "
        "AND n.responsible_id IS DISTINCT FROM d.owner_id"
    ):
        bad.append(f"open deal {r[0]}: next step is not with the owner")
    for r in await rows(
        "SELECT d.id FROM deals d WHERE d.org_id=:o AND d.deleted_at IS NULL AND ("
        "(SELECT count(*) FROM lead_pool_entries e WHERE e.deal_id=d.id AND e.left_reason='assigned') <> "
        "(SELECT count(*) FROM audit_log a WHERE a.entity_id=d.id::text AND a.outcome='ok' AND "
        " a.old_value->>'status'='pooled' AND a.new_value->>'status'='open'))"
    ):
        bad.append(f"deal {r[0]}: pool entries closed as assigned != audit rows for pooled->open")
    for r in await rows(
        "SELECT user_id, dedupe_key, count(*) FROM notifications WHERE org_id=:o AND dedupe_key IS NOT NULL "
        "GROUP BY 1,2 HAVING count(*) > 1"
    ):
        bad.append(f"duplicate notification {r[0]} {r[1]} x{r[2]}")
    for r in await rows(
        "SELECT a.id FROM accounts a WHERE a.org_id=:o AND a.merged_into_id IS NOT NULL AND "
        "(EXISTS (SELECT 1 FROM deals d WHERE d.account_id=a.id) OR "
        " EXISTS (SELECT 1 FROM contacts c WHERE c.account_id=a.id))"
    ):
        bad.append(f"merged account {r[0]} still owns deals or contacts")
    for r in await rows(
        "SELECT d.id FROM deals d JOIN accounts a ON a.id=d.account_id WHERE d.org_id=:o AND a.org_id <> d.org_id"
    ):
        bad.append(f"deal {r[0]} points at another organisation's account")
    for r in await rows(
        "SELECT d.id FROM deals d JOIN teams t ON t.id=d.team_id WHERE d.org_id=:o AND t.org_id <> d.org_id"
    ):
        bad.append(f"deal {r[0]} sits in another organisation's team")
    for r in await rows(
        "SELECT e.deal_id FROM lead_pool_entries e JOIN teams t ON t.id=e.routed_team_id WHERE e.org_id=:o AND t.org_id <> e.org_id"
    ):
        bad.append(f"pool entry of {r[0]} is routed to another organisation's team")
    for r in await rows(
        "SELECT t.id FROM transfer_requests t WHERE t.org_id=:o AND t.status='open' AND "
        "(SELECT count(*) FROM transfer_requests t2 WHERE t2.deal_id=t.deal_id AND t2.status='open') > 1"
    ):
        bad.append(f"two open transfers for one deal ({r[0]})")
    for r in await rows("SELECT count(*) FROM outbox_events WHERE org_id=:o AND status='failed'"):
        if r[0]:
            bad.append(f"{r[0]} outbox events failed permanently")
    return bad
