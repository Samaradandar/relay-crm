# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 8: seeded model-based fuzz. Random legal and illegal actions by
random roles, then the database invariants (PRD 12/13, ADR 0005) must still hold."""

import random
import uuid
from collections import Counter

import pytest

from tests.deep.phase3.invariants import check_invariants
from tests.deep.phase3.p3 import (
    STAGE_KEYS,
    H,
    Rig,
    configure,
    due,
    force_stage,
    make_file,
    rows,
    set_owner,
    setup_catalog,
)

SEEDS = (11, 23, 37)
N_ACTIONS = 560
STAGES = ["lead", "qualification", "meeting_scheduled", "prospecting", "proposal", "revision", "revision_2", "closure", "onboarding"]  # fmt: skip
PATCH_VALUES = {
    "pain_point": ["Missed calls", "", "   ", None, "x" * 300],
    "use_case": ["Order status", None, "นมัสเต 🙏"],
    "budget_amount_paise": [0, 1, 5_000_000, None, 10**12],
    "budget_source": ["Annual", None],
    "expected_close_date": ["+30", "+60", None],
    "expected_close_reason": ["Quarter end", None],
    "pricing_model": ["usage", "fixed", None],
    "plan": ["Growth", None],
    "expected_minutes_per_month": [0, 1000, None],
    "feedback": ["Accepted", "Changes requested", "Objections", None],
    "decision_date": ["+10", None],
    "final_price_paise": [0, 550000, None],
    "scope": ["Voice agent", None],
    "agreed_by": ["Asha", None],
    "billing_contact": ["a@b.in", None],
    "contract_term": ["12 months", None],
    "billing_frequency": ["monthly", None],
    "renewal_date": ["+380", None],
    "subscription_start_date": ["+15", None],
    "go_live_date": ["+7", None],
    "live_date": ["+8", None],
    "live_note": ["live", None],
    "lead_source": ["Website", "Referral", "Event", None],
    "next_step_text": ["Call back", "x" * 600, None],
    "next_step_due_date": ["+2", "-1", None],
}


def resolve(v):
    if isinstance(v, str) and v[:1] in "+-" and v[1:].isdigit():
        return due(int(v))
    return v


class Fuzz:
    def __init__(self, seed, client, world, eng):
        self.rng = random.Random(seed)
        self.client, self.world, self.eng = client, world, eng
        self.deals: list[uuid.UUID] = []
        self.approvals: list[str] = []
        self.problems: list[str] = []
        self.stats: Counter = Counter()
        self.product = None
        self.fit = None
        self.n_company = 0
        self.calls = 0
        self.actors = world.all_actors

    # ------------------------------------------------------------------ plumbing
    async def call(
        self, kind, method, url, actor, *, json=None, params=None, exact_audit=False, deal=None
    ):
        self.calls += 1
        before = await self.count_ok(deal) if (exact_audit and deal) else None
        try:
            r = await self.client.request(method, url, headers=H(actor), json=json, params=params)
        except Exception as e:  # noqa: BLE001
            self.problems.append(
                f"{kind}: {method} {url} raised {type(e).__name__}: {str(e)[:120]}"
            )
            self.stats[f"{kind}:EXC"] += 1
            return None
        self.stats[f"{kind}:{r.status_code}"] += 1
        if r.status_code >= 500:
            self.problems.append(f"{kind}: {method} {url} -> {r.status_code}")
        if r.status_code in (200, 201, 204) and exact_audit and deal:
            n = (await self.count_ok(deal)) - before
            if n != 1:
                self.problems.append(
                    f"{kind}: {method} {url} wrote {n} audit rows (expected exactly 1)"
                )
        if r.status_code == 202:
            try:
                self.approvals.append(r.json()["approval_id"])
            except Exception:  # noqa: BLE001
                pass
        return r

    async def count_ok(self, deal) -> int:
        r = await rows(
            self.eng,
            "SELECT count(*) n FROM audit_log WHERE entity_id=:d AND outcome='ok'",
            d=str(deal),
        )
        return r[0]["n"]

    def pick_deal(self):
        return self.rng.choice(self.deals) if self.deals else None

    def actor_for(self, deal_owner=None):
        if deal_owner is not None and self.rng.random() < 0.7:
            return deal_owner
        return self.rng.choice(self.actors)

    async def owner_of(self, did):
        r = await rows(self.eng, "SELECT owner_id FROM deals WHERE id=:d", d=did)
        uid = r[0]["owner_id"] if r else None
        return next((a for a in self.actors if a.id == uid), None)

    async def stage_of(self, did):
        r = await rows(self.eng, "SELECT stage_key, status FROM deals WHERE id=:d", d=did)
        return (r[0]["stage_key"], r[0]["status"]) if r else (None, None)

    # ------------------------------------------------------------------ setup
    async def setup(self):
        await configure(self.client, self.world, cap=500)
        self.product = await setup_catalog(self.client, self.world)
        self.fit = (
            await self.client.get("/settings/fit-checklist", headers=H(self.world.admin))
        ).json()
        for _ in range(14):
            await self.create_lead()
        for did in self.deals[:8]:  # a few deals start deeper in the pipeline
            owner = await self.owner_of(did)
            if owner is not None:
                await force_stage(
                    self.eng,
                    self.world.org_id,
                    did,
                    self.rng.choice(STAGES[:8]),
                    round_=2,
                    ticker=owner,
                )

    # ------------------------------------------------------------------ actions
    async def create_lead(self):
        self.n_company += 1
        actor = self.rng.choice(
            [
                self.world.north1,
                self.world.north2,
                self.world.south1,
                self.world.tl_north,
                self.world.admin,
                self.world.sa,
            ]
        )
        body = {"company": f"Fuzz Co {self.n_company} {self.rng.randint(0, 10**6)}", "contact_name": "Asha", "email": f"f{self.n_company}{self.rng.randint(0, 10**6)}@example.com", "lead_source": "Website", "next_step_text": "Call", "next_step_due_date": due(1), "reason": "fuzz"}  # fmt: skip
        r = await self.call("create_lead", "POST", "/leads", actor, json=body)
        if r is not None and r.status_code == 201:
            self.deals.append(uuid.UUID(r.json()["id"]))

    async def log_activity(self):
        did = self.pick_deal()
        owner = await self.owner_of(did)
        t = self.rng.choice(
            ["call", "email", "meeting", "walkthrough", "kickoff", "note", "discovery"]
        )
        body = {"type": t, "two_way": self.rng.choice([True, False, None]), "discussed": self.rng.choice(["Talked", "  ", "notes"]), "attendees": self.rng.choice([[], ["A", "B"]]), "next_step_text": "Follow up", "next_step_due_date": due(self.rng.choice([-1, 0, 2, 9])), "reason": "fuzz"}  # fmt: skip
        if self.rng.random() < 0.4:
            body["occurred_at"] = resolve(self.rng.choice(["-3", "+2", "-30"])) + "T10:00:00+05:30"
        await self.call(
            "log_activity",
            "POST",
            f"/deals/{did}/activities",
            self.actor_for(owner),
            json=body,
            deal=did,
        )

    async def patch(self):
        did = self.pick_deal()
        owner = await self.owner_of(did)
        body = {}
        for _ in range(self.rng.randint(1, 3)):
            f = self.rng.choice(list(PATCH_VALUES))
            body[f] = resolve(self.rng.choice(PATCH_VALUES[f]))
        if self.rng.random() < 0.7:
            body["reason"] = "fuzz"
        if self.rng.random() < 0.2:
            body["fit_checklist"] = {
                self.rng.choice(self.fit)["id"]: self.rng.choice([True, False])
            }
        await self.call("patch", "PATCH", f"/deals/{did}", self.actor_for(owner), json=body)

    async def tick(self):
        did = self.pick_deal()
        stage, _ = await self.stage_of(did)
        if stage is None:
            return
        key = self.rng.choice(STAGE_KEYS[stage])
        owner = await self.owner_of(did)
        await self.call(
            "tick",
            "POST",
            f"/deals/{did}/criteria/{key}/tick",
            self.actor_for(owner),
            json={"reason": "fuzz"} if self.rng.random() < 0.3 else {},
            deal=did,
            exact_audit=True,
        )

    async def untick_verify(self):
        did = self.pick_deal()
        stage, _ = await self.stage_of(did)
        if stage is None:
            return
        key = self.rng.choice(STAGE_KEYS[stage])
        owner = await self.owner_of(did)
        kind = self.rng.choice(["untick", "verify", "reject", "review"])
        a = self.actor_for(owner)
        if kind == "untick":
            await self.call(
                kind,
                "POST",
                f"/deals/{did}/criteria/{key}/untick",
                a,
                json={"reason": "fuzz"},
                deal=did,
                exact_audit=True,
            )
        elif kind == "review":
            await self.call(
                kind,
                "POST",
                f"/deals/{did}/review-done",
                a,
                json={"reason": "fuzz"},
                deal=did,
                exact_audit=True,
            )
        else:
            await self.call(
                kind,
                "POST",
                f"/deals/{did}/criteria/{key}/verify",
                a,
                json={"decision": "verify" if kind == "verify" else "reject", "reason": "fuzz"},
                deal=did,
                exact_audit=True,
            )

    async def move(self):
        did = self.pick_deal()
        owner = await self.owner_of(did)
        body = {"to_stage": self.rng.choice(STAGES + ["nowhere"])}
        if self.rng.random() < 0.6:
            body["reason"] = "fuzz"
        if self.rng.random() < 0.3:
            body["override"] = True
        await self.call(
            "move",
            "POST",
            f"/deals/{did}/move",
            self.actor_for(owner),
            json=body,
            deal=did,
            exact_audit=True,
        )

    async def lines(self):
        did = self.pick_deal()
        owner = await self.owner_of(did)
        existing = await rows(
            self.eng, "SELECT id FROM deal_products WHERE deal_id=:d AND deleted_at IS NULL", d=did
        )
        a = self.actor_for(owner)
        op = self.rng.choice(["add", "add", "patch", "delete"])
        if op == "add" or not existing:
            body = {
                "product_id": self.product["id"],
                "quantity": self.rng.choice([1, 10, 1000, 1_000_000]),
                "discount_pct": str(self.rng.choice([0, 0, 5, 10, 12, 33.33])),
                "reason": "fuzz",
            }
            await self.call("line_add", "POST", f"/deals/{did}/lines", a, json=body)
        elif op == "patch":
            body = {"quantity": self.rng.choice([1, 50, 1000, 2000]), "reason": "fuzz"}
            if self.rng.random() < 0.5:
                body["discount_pct"] = str(self.rng.choice([0, 3, 10, 15]))
            await self.call(
                "line_patch",
                "PATCH",
                f"/deals/{did}/lines/{self.rng.choice(existing)['id']}",
                a,
                json=body,
            )
        else:
            await self.call(
                "line_delete",
                "DELETE",
                f"/deals/{did}/lines/{self.rng.choice(existing)['id']}",
                a,
                params={"reason": "fuzz"},
            )

    async def approvals_decide(self):
        if not self.approvals:
            return
        ap = self.rng.choice(self.approvals)
        a = self.rng.choice(self.actors)
        if self.rng.random() < 0.15:
            await self.call("cancel", "POST", f"/approvals/{ap}/cancel", a)
        else:
            await self.call(
                "decide",
                "POST",
                f"/approvals/{ap}/decide",
                a,
                json={
                    "decision": self.rng.choice(["approve", "approve", "reject"]),
                    "reason": "fuzz",
                },
            )

    async def documents(self):
        did = self.pick_deal()
        owner = await self.owner_of(did)
        a = self.actor_for(owner)
        existing = await rows(
            self.eng,
            "SELECT id, kind FROM documents WHERE deal_id=:d AND deleted_at IS NULL",
            d=did,
        )
        op = self.rng.choice(["add", "add", "patch", "delete"])
        if op == "add" or not existing:
            kind = self.rng.choice(["proposal", "agreement", "invoice"])
            status = self.rng.choice(["draft", "sent", "countersigned", "terminated"])
            fid = None
            if self.rng.random() < 0.7 and owner is not None:
                fid = await make_file(self.eng, self.world, did, owner)
            await self.call(
                "doc_add",
                "POST",
                f"/deals/{did}/documents",
                a,
                json={
                    "kind": kind,
                    "status": status,
                    "file_id": fid,
                    "number": self.rng.choice([None, "INV-1", "=1+1"]),
                    "reason": "fuzz",
                },
            )
        elif op == "patch":
            d = self.rng.choice(existing)
            await self.call(
                "doc_patch",
                "PATCH",
                f"/documents/{d['id']}",
                a,
                json={
                    self.rng.choice(["status", "notes", "number"]): self.rng.choice(
                        ["draft", "sent", "countersigned", "terminated", "n", "N-2"]
                    ),
                    "reason": "fuzz",
                },
            )
        else:
            await self.call(
                "doc_delete",
                "DELETE",
                f"/documents/{self.rng.choice(existing)['id']}",
                a,
                params={"reason": "fuzz"},
            )

    async def lifecycle(self):
        did = self.pick_deal()
        owner = await self.owner_of(did)
        a = self.actor_for(owner)
        op = self.rng.choice(["lose", "lose", "reopen", "delete", "restore", "bad_lose"])
        if op == "lose":
            await self.call(
                op,
                "POST",
                f"/deals/{did}/lose",
                a,
                json={
                    "lost_reason": self.rng.choice(["Price", "Timing", "Other"]),
                    "notes": "fuzz",
                },
                deal=did,
                exact_audit=True,
            )
        elif op == "bad_lose":
            await self.call(op, "POST", f"/deals/{did}/lose", a, json={"lost_reason": "Nope"})
        elif op == "reopen":
            await self.call(
                op,
                "POST",
                f"/deals/{did}/reopen",
                a,
                json={"reason": "fuzz"},
                deal=did,
                exact_audit=True,
            )
        elif op == "delete":
            await self.call(
                op,
                "POST",
                f"/deals/{did}/delete",
                a,
                json={"reason": "fuzz"},
                deal=did,
                exact_audit=True,
            )
        else:
            await self.call(
                op,
                "POST",
                f"/deals/{did}/restore",
                a,
                json={"reason": "fuzz"},
                deal=did,
                exact_audit=True,
            )

    async def bulk(self):
        ids = self.rng.sample(self.deals, k=min(len(self.deals), self.rng.randint(1, 5)))
        a = self.rng.choice(
            [
                self.world.tl_north,
                self.world.tl_south,
                self.world.admin,
                self.world.sa,
                self.world.north1,
            ]
        )
        op = self.rng.choice(["lose", "snooze", "reassign"])
        if op == "lose":
            await self.call(
                "bulk_lose",
                "POST",
                "/deals/bulk/lose",
                a,
                json={"deal_ids": [str(i) for i in ids], "lost_reason": "Price", "reason": "fuzz"},
            )
        elif op == "snooze":
            await self.call(
                "bulk_snooze",
                "POST",
                "/deals/bulk/snooze",
                a,
                json={"deal_ids": [str(i) for i in ids], "reason": "fuzz"},
            )
        else:
            to = self.rng.choice([self.world.north1, self.world.north2, self.world.south1])
            await self.call(
                "bulk_reassign",
                "POST",
                "/deals/bulk/reassign",
                a,
                json={"deal_ids": [str(i) for i in ids], "user_id": str(to.id), "reason": "fuzz"},
            )

    async def next_steps(self):
        did = self.pick_deal()
        owner = await self.owner_of(did)
        a = self.actor_for(owner)
        step = await rows(
            self.eng, "SELECT id FROM next_steps WHERE deal_id=:d AND done_at IS NULL", d=did
        )
        op = self.rng.choice(["set", "set", "done", "snooze", "replied", "nudge"])
        if op == "set" or not step:
            body = {
                "text": "Next",
                "due_date": due(self.rng.choice([-1, 0, 3])),
                "lane": self.rng.choice(["your_move", "waiting_on_them"]),
                "reason": "fuzz",
            }
            if self.rng.random() < 0.2:
                body["responsible_id"] = str(self.rng.choice(self.actors).id)
            await self.call("step_set", "POST", f"/deals/{did}/next-step", a, json=body)
        elif op == "done":
            await self.call(
                "step_done",
                "POST",
                f"/next-steps/{step[0]['id']}/done",
                a,
                json={"next": {"text": "Then", "due_date": due(2), "reason": "fuzz"}},
            )
        else:
            await self.call(
                "step_" + op,
                "POST",
                f"/next-steps/{step[0]['id']}/{op}",
                a,
                json={"reason": "fuzz"},
            )

    async def activity_edit_delete(self):
        did = self.pick_deal()
        acts = await rows(
            self.eng, "SELECT id FROM activities WHERE deal_id=:d AND deleted_at IS NULL", d=did
        )
        if not acts:
            return
        aid = self.rng.choice(acts)["id"]
        a = self.rng.choice(self.actors)
        if self.rng.random() < 0.5:
            await self.call(
                "act_edit",
                "PATCH",
                f"/activities/{aid}",
                a,
                json={"discussed": "edited " + str(self.rng.randint(0, 99)), "reason": "fuzz"},
            )
        else:
            await self.call(
                "act_delete", "DELETE", f"/activities/{aid}", a, params={"reason": "fuzz"}
            )

    async def config_publish(self):
        a = self.rng.choice([self.world.admin, self.world.sa, self.world.tl_north])
        r = await self.call(
            "cfg_draft",
            "POST",
            "/pipelines/new_business/versions",
            a,
            json={"from_version_no": 1, "reason": "fuzz"},
        )
        if r is not None and r.status_code == 200:
            v = r.json()["version_no"]
            await self.call(
                "cfg_publish",
                "POST",
                f"/pipelines/new_business/versions/{v}/publish",
                a,
                json={"reason": "fuzz"},
            )

    async def boost(self):
        did = self.pick_deal()
        stage, status = await self.stage_of(did)
        owner = await self.owner_of(did)
        if status == "open" and owner is not None:
            await force_stage(
                self.eng,
                self.world.org_id,
                did,
                self.rng.choice(STAGES[:8]),
                round_=2,
                ticker=owner,
            )
            await set_owner(self.eng, did, owner)

    async def prove(self):
        did = self.pick_deal()
        stage, status = await self.stage_of(did)
        owner = await self.owner_of(did)
        if status != "open" or owner is None:
            return
        rig = Rig(self.client, self.world, self.eng)
        rig.did, rig.owner, rig.product, rig.fit = str(did), owner, self.product, self.fit
        try:
            await rig.prove("revision" if stage == "revision_2" else stage)
        except AssertionError:
            self.stats["prove:blocked"] += 1
        except Exception as e:  # noqa: BLE001
            self.problems.append(f"prove {stage}: raised {type(e).__name__}: {str(e)[:100]}")

    async def run(self, n):
        menu = [
            (self.create_lead, 4), (self.log_activity, 9), (self.patch, 9), (self.tick, 9), (self.untick_verify, 6),
            (self.move, 12), (self.lines, 7), (self.approvals_decide, 5), (self.documents, 6), (self.lifecycle, 6),
            (self.bulk, 3), (self.next_steps, 6), (self.activity_edit_delete, 4), (self.config_publish, 1),
            (self.boost, 2), (self.prove, 10),
        ]  # fmt: skip
        fns = [f for f, w in menu for _ in range(w)]
        guard = 0
        while self.calls < n and guard < n * 6:
            guard += 1
            await self.rng.choice(fns)()


@pytest.mark.parametrize("seed", SEEDS)
async def test_fuzz_random_actions_keep_every_invariant(client, world, admin_engine, seed):
    fz = Fuzz(seed, client, world, admin_engine)
    await fz.setup()
    await fz.run(N_ACTIONS)
    s = fz.stats
    total = sum(s.values())
    assert fz.calls >= N_ACTIONS and total >= N_ACTIONS, (fz.calls, total)
    ok_writes = sum(v for k, v in s.items() if k.split(":")[-1] in ("200", "201", "204", "202"))
    assert ok_writes > 60, f"the fuzz hardly did anything: {dict(s)}"  # it must reach real states
    assert not fz.problems, "\n".join(fz.problems[:25])
    bad = await check_invariants(admin_engine, world.org_id)
    assert not bad, "\n".join(bad[:25])
    # reached the interesting states
    moved = sum(v for k, v in s.items() if k.startswith("move:200"))
    assert moved >= 1, dict(s)
    print("FUZZ", seed, dict(sorted(s.items())))
