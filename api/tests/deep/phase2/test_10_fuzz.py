# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 deep test, section 8: model-based fuzz. A seeded random sequence of legal and illegal
actions by random roles, then every invariant is checked and nothing may have returned a 500.

Known bugs that return 500 on purpose-built input (BUG-P2-15, -22, -35 ...) are steered around so
the fuzz can find NEW problems; the steered-around inputs have their own xfail tests."""

import random
import uuid

import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.platform.audit.service import verify_chain
from tests.deep.phase2.support import headers, invariant_violations, q
from tests.helpers import dispatch_all

SOURCES = ["Website", "Referral", "LinkedIn", "Event", "Cold outbound", "Inbound call"]


class Fuzz:
    def __init__(self, qc, engine, world, seed: int) -> None:
        self.qc, self.engine, self.world = qc, engine, world
        self.rng = random.Random(seed)
        self.actors = list(world.all_actors)
        self.codes: list[tuple[str, int]] = []
        self.counter = 0
        self.last_edit: list[tuple[str, str, object]] = []  # (deal id, audit id, actor)
        self.names: list[str] = []

    # --------------------------------------------------------------- helpers
    async def call(self, label, method, url, actor, body=None):
        r = await self.qc.request(method, url, json=body, headers=headers(actor))
        self.codes.append((label, r.status_code))
        assert r.status_code < 500, (label, method, url, body, r.status_code, r.text[:300])
        return r

    def actor(self, *pool):
        return self.rng.choice(pool or self.actors)

    async def ids(self, sql, **p):
        return [r[0] for r in await q(self.engine, sql, o=self.world.org_id, **p)]

    def reason(self, p=0.7):
        return "fuzz reason" if self.rng.random() < p else None

    def user(self):
        return self.rng.choice(self.actors + [self.world.north1, self.world.south1])

    # --------------------------------------------------------------- actions
    async def create(self):
        self.counter += 1
        base = self.rng.choice(
            ["Alpha", "Bravo", "Cinder", "Delphi", "Ember", "Fjord", "Gamma", "Helix"]
        )
        reuse = self.names and self.rng.random() < 0.25
        name = (
            self.rng.choice(self.names)
            if reuse
            else f"{base} {uuid.UUID(int=self.rng.getrandbits(128)).hex[:8]} Works"
        )
        body = {
            "company": name,
            "website": f"https://{uuid.UUID(int=self.rng.getrandbits(128)).hex[:10]}.example"
            if not reuse or self.rng.random() < 0.5
            else None,
            "contact_name": "Fuzz Contact",
            "email": f"f{uuid.UUID(int=self.rng.getrandbits(128)).hex[:10]}@example.com",
            "lead_source": self.rng.choice(SOURCES),
            "next_step_text": "Call",
            "next_step_due_date": "2099-01-01",
            "create_anyway": self.rng.random() < 0.5,
            "reason": self.reason(0.6),
        }
        if body["website"] is None:
            body.pop("website")
        r = await self.call("create", "POST", "/leads", self.actor(), body)
        if r.status_code == 201:
            self.names.append(name)

    async def assign(self):
        deals = await self.ids(
            "SELECT id FROM deals WHERE org_id=:o AND status='pooled' AND deleted_at IS NULL"
        )
        if deals:
            await self.call(
                "assign",
                "POST",
                f"/pool/{self.rng.choice(deals)}/assign",
                self.actor(),
                {"user_id": str(self.user().id), "reason": self.reason()},
            )

    async def reassign(self):
        deals = await self.ids(
            "SELECT id FROM deals WHERE org_id=:o AND status='open' AND deleted_at IS NULL"
        )
        if deals:
            await self.call(
                "reassign",
                "POST",
                f"/deals/{self.rng.choice(deals)}/reassign",
                self.actor(),
                {"user_id": str(self.user().id), "reason": self.reason()},
            )

    async def bulk(self):
        a, b = self.user(), self.user()
        body = {"from_user_id": str(a.id), "reason": self.reason()}
        if self.rng.random() < 0.5:
            body["spread"] = True
        else:
            body["to_user_id"] = str(b.id)
        await self.call("bulk", "POST", "/deals/bulk-reassign", self.actor(), body)

    async def transfer(self):
        deals = await self.ids(
            "SELECT id FROM deals WHERE org_id=:o AND status='open' AND deleted_at IS NULL"
        )
        if not deals:
            return
        did = str(self.rng.choice(deals))
        tl = self.actor(
            self.world.tl_north, self.world.tl_south, self.world.north1, self.world.admin
        )
        if self.rng.random() < 0.5:
            body = {"direction": "take", "deal_id": did, "reason": self.reason(0.8)}
            if self.rng.random() < 0.7:
                body["owner_id"] = str(
                    self.rng.choice(
                        [self.world.south1, self.world.south2, self.world.north1, self.world.north2]
                    ).id
                )
        else:
            body = {
                "direction": "give",
                "deal_id": did,
                "to_team_id": str(self.rng.choice([self.world.north_team, self.world.south_team])),
                "reason": self.reason(0.8),
            }
        await self.call("transfer", "POST", "/transfers", tl, body)

    async def decide(self):
        rows = await q(
            self.engine,
            "SELECT id, requested_by FROM approval_requests WHERE org_id=:o AND status='pending' AND type='deal.team_transfer'",
            o=self.world.org_id,
        )
        if not rows:
            return
        aid, requester = self.rng.choice(rows)
        if self.rng.random() < 0.2:
            actor = next(a for a in self.actors if a.id == requester)
            await self.call("cancel", "POST", f"/approvals/{aid}/cancel", actor)
            return
        await self.call(
            "decide",
            "POST",
            f"/approvals/{aid}/decide",
            self.actor(self.world.admin, self.world.sa, self.world.tl_north, self.world.tl_south),
            {
                "decision": self.rng.choice(["approve", "approve", "reject"]),
                "reason": self.reason(0.8),
            },
        )

    async def comment(self):
        rows = await self.ids("SELECT id FROM transfer_requests WHERE org_id=:o AND status='open'")
        if rows:
            await self.call(
                "comment",
                "POST",
                f"/transfers/{self.rng.choice(rows)}/comment",
                self.actor(),
                {"comment": "fuzz comment"},
            )

    async def merge(self):
        accs = await self.ids(
            "SELECT id FROM accounts WHERE org_id=:o AND merged_into_id IS NULL AND domain IS NOT NULL"
        )
        if len(accs) >= 2:
            a, b = self.rng.sample(accs, 2)
            await self.call(
                "merge",
                "POST",
                f"/accounts/{a}/merge",
                self.actor(self.world.admin, self.world.sa, self.world.tl_north, self.world.north1),
                {"into_account_id": str(b), "reason": self.reason(0.8)},
            )

    async def edit(self):
        deals = await self.ids("SELECT id FROM deals WHERE org_id=:o AND deleted_at IS NULL")
        if not deals:
            return
        did = self.rng.choice(deals)
        actor = self.actor()
        body = self.rng.choice(
            [
                {"lead_source": self.rng.choice(SOURCES)},
                {"next_step_text": f"Step {self.rng.randint(1, 99)}"},
                {"first_contact_date": "2026-01-01"},
                {"next_step_due_date": "2099-02-02"},
                {"lead_source": None},
            ]
        )
        body["reason"] = self.reason(0.7)
        r = await self.call("edit", "PATCH", f"/deals/{did}", actor, body)
        if r.status_code == 200 and r.json().get("last_audit_id"):
            self.last_edit.append((str(did), r.json()["last_audit_id"], actor))

    async def undo(self):
        if self.last_edit:
            did, aid, actor = self.rng.choice(self.last_edit[-5:])
            who = actor if self.rng.random() < 0.8 else self.actor()
            await self.call("undo", "POST", f"/deals/{did}/undo", who, {"audit_id": aid})

    async def settings(self):
        rules = []
        for i in range(self.rng.randint(0, 3)):
            kind = self.rng.choice(["team", "user"])
            target = (
                self.rng.choice([self.world.north_team, self.world.south_team])
                if kind == "team"
                else self.rng.choice(
                    [self.world.north1, self.world.north2, self.world.south1, self.world.south2]
                ).id
            )
            match = self.rng.choice(
                [
                    {},
                    {"lead_source": self.rng.choice(SOURCES)},
                    {"lead_source": self.rng.choice(SOURCES).lower()},
                ]
            )
            rules.append(
                {
                    "name": f"r{i}",
                    "priority": self.rng.randint(0, 5),
                    "active": self.rng.random() < 0.8,
                    "match": match,
                    "target_type": kind,
                    "target_id": str(target),
                }
            )
        caps = {
            str(u.id): self.rng.randint(0, 6)
            for u in (self.world.north1, self.world.north2, self.world.south1, self.world.south2)
            if self.rng.random() < 0.5
        }
        body = {
            "default_team_id": str(self.rng.choice([self.world.north_team, self.world.south_team])),
            "default_cap": self.rng.randint(0, 6),
            "rules": rules,
            "caps": caps,
            "reason": self.reason(0.8),
        }
        await self.call(
            "settings",
            "PUT",
            "/settings/assignment",
            self.actor(self.world.admin, self.world.sa, self.world.tl_north),
            body,
        )

    async def deactivate(self):
        rep = self.rng.choice(
            [self.world.north1, self.world.north2, self.world.south1, self.world.south2]
        )
        body = {"is_active": self.rng.random() < 0.5, "reason": "fuzz"}
        if self.rng.random() < 0.3:
            body = {
                "team_id": str(self.rng.choice([self.world.north_team, self.world.south_team])),
                "open_deals": self.rng.choice(["move", "return_to_pool"]),
                "reason": "fuzz",
            }
        await self.call("user", "PATCH", f"/users/{rep.id}", self.world.admin, body)

    async def close(self):
        deals = await self.ids(
            "SELECT id FROM deals WHERE org_id=:o AND status='open' AND deleted_at IS NULL"
        )
        if not deals:
            return
        did = self.rng.choice(deals)
        if self.rng.random() < 0.6:
            await self.call(
                "lose", "POST", f"/deals/{did}/lose", self.actor(), {"lost_reason": "Price"}
            )
        else:
            await self.call(
                "delete",
                "POST",
                f"/deals/{did}/delete",
                self.actor(self.world.admin, self.world.sa, self.world.tl_north),
                {"reason": self.reason(0.8)},
            )

    async def restore(self):
        deals = await self.ids("SELECT id FROM deals WHERE org_id=:o AND deleted_at IS NOT NULL")
        if deals:
            await self.call(
                "restore",
                "POST",
                f"/deals/{self.rng.choice(deals)}/restore",
                self.actor(self.world.admin, self.world.sa),
                {"reason": "fuzz"},
            )

    async def move_team(self):
        deals = await self.ids(
            "SELECT id FROM deals WHERE org_id=:o AND status IN ('open','pooled') AND deleted_at IS NULL"
        )
        if deals:
            team = self.rng.choice([self.world.north_team, self.world.south_team])
            owner = self.rng.choice(
                [self.world.north1, self.world.north2, self.world.south1, self.world.south2]
            )
            await self.call(
                "move",
                "POST",
                f"/deals/{self.rng.choice(deals)}/move-team",
                self.actor(self.world.admin, self.world.sa, self.world.tl_south),
                {"to_team_id": str(team), "owner_id": str(owner.id), "reason": self.reason(0.8)},
            )

    async def check(self):
        a = self.actor()
        await self.call(
            "check",
            "POST",
            "/duplicates/check",
            a,
            {"company": self.rng.choice(self.names or ["x"]), "phone": None},
        )

    async def reads(self):
        a = self.actor()
        for url in (
            "/pool",
            "/pool/distribution",
            "/deals",
            "/transfers",
            "/accounts?q=a",
            "/approvals",
        ):
            await self.call("read", "GET", url, a)

    async def run(self, n=300):
        weighted = (
            [self.create] * 6
            + [self.assign] * 2
            + [self.reassign] * 3
            + [self.bulk]
            + [self.transfer] * 3
            + [self.decide] * 3
            + [self.comment]
            + [self.merge] * 2
            + [self.edit] * 4
            + [self.undo] * 2
            + [self.settings] * 2
            + [self.deactivate] * 2
            + [self.close] * 2
            + [self.restore]
            + [self.move_team]
            + [self.check]
            + [self.reads]
        )
        for i in range(n):
            await self.rng.choice(weighted)()
            if i % 25 == 24:
                await dispatch_all()
        await dispatch_all()
        await dispatch_all()


@pytest.mark.parametrize("seed", [7, 11, 2026])
async def test_three_hundred_random_actions_keep_every_invariant(qc, admin_engine, world, seed):
    await qc.put(
        "/settings/assignment",
        json={"default_team_id": str(world.north_team), "default_cap": 4, "reason": "setup"},
        headers=headers(world.admin),
    )
    fz = Fuzz(qc, admin_engine, world, seed)
    await fz.run(300)
    bad = await invariant_violations(admin_engine, world.org_id)
    assert not bad, bad[:10]
    async with get_sessionmaker()() as s:
        res = await verify_chain(s, world.org_id)
    assert res.ok, res
    ok = {c for _, c in fz.codes}
    assert ok & {200, 201, 202, 204}, "the fuzz produced no successful call"
    assert ok & {403, 404, 409, 422}, "the fuzz never hit a refusal"
    deals = await q(
        admin_engine,
        "SELECT status, count(*) FROM deals WHERE org_id=:o GROUP BY 1",
        o=world.org_id,
    )
    from collections import Counter

    print("FUZZ", seed, dict(deals), dict(sorted(Counter(fz.codes).items())))
    # every successful create left exactly one lead.create row for its deal
    n = await q(
        admin_engine,
        "SELECT count(*) FROM deals d WHERE d.org_id=:o AND (SELECT count(*) FROM audit_log a WHERE a.entity_id=d.id::text AND a.action_key='lead.create' AND a.outcome='ok') <> 1",
        o=world.org_id,
    )
    assert n[0][0] == 0
    # every audit row of this org belongs to this org's entities and hash chain
    assert (
        await q(
            admin_engine,
            "SELECT 1 FROM audit_log WHERE org_id=:o AND outcome='ok' AND actor_id IS NOT NULL AND actor_id NOT IN (SELECT id FROM users WHERE org_id=:o) LIMIT 1",
            o=world.org_id,
        )
        == []
    )
    _ = text
