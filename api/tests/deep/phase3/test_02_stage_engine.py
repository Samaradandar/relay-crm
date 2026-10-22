# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 2: the stage engine, every (from, to) pair x role x state.

The expected table is written from the PRD (sections 4.2, 4.4 D, 9, 10), not from the code."""

import pytest

from tests.deep.phase3.p3 import (
    CONDITIONAL,
    FORWARD,
    ORDER,
    POS,
    H,
    add_proposal_doc,
    audit_rows,
    events_after,
    force_stage,
    max_event,
    max_seq,
    mkdeal,
    one,
    rows,
    run,
    set_data,
    set_owner,
)


def classify(frm: str, to: str, override: bool) -> str:
    if (frm, to) in CONDITIONAL:
        return "conditional"
    if frm == to:
        return "same"
    if POS[to] < POS[frm]:
        return "backward"
    if (frm, to) in FORWARD and not override:
        return "forward"
    return "override" if (frm, to) in FORWARD else "skip"


AUDIT_KEY = {
    "forward": "deal.move_forward",
    "conditional": "deal.move_forward",
    "backward": "deal.move_backward",
    "override": "deal.override_or_skip",
    "skip": "deal.override_or_skip",
}
EXIT_KIND = {
    "forward": "forward",
    "conditional": "forward",
    "backward": "backward",
    "override": "override",
    "skip": "skip",
}


def allowed(role: str, is_owner: bool, same_team: bool, kind: str) -> bool:
    """PRD 4.4 D: who may do which kind of move."""
    if kind in ("forward", "conditional"):
        return is_owner  # owner only, for every role, also direct API
    if kind == "backward":
        return {"user": is_owner, "team_leader": same_team, "admin": True, "super_admin": True}[
            role
        ]
    return {
        "user": False,
        "team_leader": same_team and not is_owner,
        "admin": True,
        "super_admin": True,
    }[role]


def reason_needed(role: str, kind: str) -> bool:
    if kind in ("conditional", "override", "skip"):
        return True
    if kind == "backward":
        return role != "super_admin"
    return role == "admin"  # a normal forward move is routine: only the Admin must explain


def kinds_table(world):
    return {
        "user_owner": (world.north1, world.north1),
        "user_same_team": (world.north2, world.north1),
        "user_other_team": (world.south1, world.north1),
        "tl_own_team": (world.tl_north, world.north1),
        "tl_owner": (world.tl_north, world.tl_north),
        "tl_other_team": (world.tl_south, world.north1),
        "admin": (world.admin, world.north1),
        "admin_owner": (world.admin, world.admin),
        "sa": (world.sa, world.north1),
        "sa_owner": (world.sa, world.sa),
    }


async def prep(client, world, eng):
    did, _o = await mkdeal(client, world, eng)
    await add_proposal_doc(eng, world.org_id, did, world.north1)
    await set_data(eng, did, feedback="Accepted")
    return did


async def reset(eng, world, did, owner, frm, *, ticked_all=True, ticked=()):
    await set_owner(eng, did, owner)
    await run(eng, "UPDATE deals SET flags='{}'::jsonb, health='on_track' WHERE id=:d", d=did)
    return await force_stage(
        eng,
        world.org_id,
        did,
        frm,
        round_=2 if frm == "revision_2" else 1,
        all_ticked=ticked_all,
        ticked=ticked,
        ticker=owner,
    )


@pytest.mark.parametrize("frm", ORDER)
async def test_move_matrix_every_target_every_role(client, world, admin_engine, frm):
    eng = admin_engine
    did = await prep(client, world, eng)
    bad: list[str] = []
    count = 0
    for override in (False, True):
        for to in ORDER:
            kind = classify(frm, to, override)
            if override and kind not in ("override", "skip"):
                continue  # the flag only matters for forward moves
            for name, (actor, owner) in kinds_table(world).items():
                for reason in ("because", None):
                    count += 1
                    await reset(eng, world, did, owner, frm)
                    mark, ev = await max_seq(eng), await max_event(eng)
                    body = {"to_stage": to, "override": override}
                    if reason:
                        body["reason"] = reason
                    r = await client.post(f"/deals/{did}/move", json=body, headers=H(actor))
                    tag = f"{frm}->{to} override={override} {name} reason={bool(reason)}"
                    # ---- expected answer
                    if frm == "onboarding":
                        exp = (409, "conflict")
                    elif kind == "same":
                        exp = (422, "validation_error")
                    else:
                        is_owner = actor.id == owner.id
                        same_team = actor.team_id is not None and actor.team_id == owner.team_id
                        if not allowed(actor.role, is_owner, same_team, kind):
                            exp = (403, None)
                        elif reason_needed(actor.role, kind) and not reason:
                            exp = (422, "reason_required")
                        else:
                            exp = (200, None)
                    if r.status_code != exp[0] or (exp[1] and r.json().get("code") != exp[1]):
                        bad.append(f"{tag}: got {r.status_code} {r.text[:90]}, expected {exp}")
                        continue
                    stage = await one(eng, "SELECT stage_key FROM deals WHERE id=:d", d=did)
                    new_audit = [a for a in await audit_rows(eng, did) if a["seq"] > mark]
                    evs = [e["type"] for e in await events_after(eng, ev, did)]
                    n_open = await one(
                        eng,
                        "SELECT count(*) FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
                        d=did,
                    )
                    if n_open != 1:
                        bad.append(f"{tag}: {n_open} open stage entries")
                    if exp[0] != 200:
                        if stage != frm:
                            bad.append(f"{tag}: refused but the deal moved to {stage}")
                        if any(a["outcome"] == "ok" for a in new_audit) or evs:
                            bad.append(
                                f"{tag}: refused but wrote ok audit/events {new_audit} {evs}"
                            )
                        if exp[0] == 403 and [a["outcome"] for a in new_audit] != ["denied"]:
                            bad.append(
                                f"{tag}: denied move must leave exactly one denied audit row, got {new_audit}"
                            )
                        continue
                    # ---- successful move
                    if stage != to:
                        bad.append(f"{tag}: ended in {stage}")
                    oks = [a for a in new_audit if a["outcome"] == "ok"]
                    if len(oks) != 1 or len(new_audit) != 1:
                        bad.append(f"{tag}: expected exactly one audit row, got {new_audit}")
                    else:
                        if oks[0]["action_key"] != AUDIT_KEY[kind]:
                            bad.append(f"{tag}: audit key {oks[0]['action_key']}")
                        if oks[0]["reason"] != (reason or None):
                            bad.append(f"{tag}: audit reason {oks[0]['reason']!r}")
                    out = r.json()
                    if out["kind"] != kind or out["from_stage"] != frm or out["to_stage"] != to:
                        bad.append(f"{tag}: response {out}")
                    ent = await rows(
                        eng,
                        "SELECT stage_key, exited_at, exit_kind, reason FROM deal_stage_entries WHERE deal_id=:d ORDER BY entered_at, exited_at NULLS LAST",
                        d=did,
                    )
                    prev = [e for e in ent if e["exited_at"] is not None][-1]
                    cur = [e for e in ent if e["exited_at"] is None][0]
                    if (prev["stage_key"], prev["exit_kind"]) != (frm, EXIT_KIND[kind]) or cur[
                        "stage_key"
                    ] != to:
                        bad.append(f"{tag}: entries prev={prev} cur={cur}")
                    if (prev["reason"] or None) != (reason or None):
                        bad.append(f"{tag}: exit reason {prev['reason']!r}")
                    won = to == "onboarding"
                    want = ["deal.moved"] + (["deal.won", "deal.closed"] if won else [])
                    if kind in ("override", "skip") and not out["overridden"] == []:
                        want.insert(1, "deal.overridden")
                    if sorted(evs) != sorted(want):
                        bad.append(f"{tag}: events {evs}, expected {want}")
                    st = await rows(
                        eng, "SELECT status, won_at, revision_round FROM deals WHERE id=:d", d=did
                    )
                    if (st[0]["status"], st[0]["won_at"] is not None) != (
                        ("won", True) if won else ("open", False)
                    ):
                        bad.append(f"{tag}: status {st}")
                    if kind in ("forward", "backward", "override") and out["overridden"]:
                        bad.append(
                            f"{tag}: overridden list on a fully ticked deal {out['overridden']}"
                        )
                    # a fresh checklist: nothing ticked, snapshot = the target stage's criteria
                    ticks = await one(
                        eng,
                        "SELECT count(*) FROM deal_criteria c JOIN deal_stage_entries e ON e.id=c.entry_id WHERE e.deal_id=:d AND e.exited_at IS NULL",
                        d=did,
                    )
                    if ticks:
                        bad.append(f"{tag}: new visit starts with {ticks} ticks")
    assert count > 0
    assert not bad, f"{len(bad)} mismatches of {count} cases:\n" + "\n".join(bad[:25])


# --------------------------------------------------------------------------- criteria and entry checks
async def test_forward_needs_every_criterion_and_lists_what_is_missing(client, world, admin_engine):
    eng = admin_engine
    did = await prep(client, world, eng)
    owner = world.north1
    snap = [
        c["key"]
        for c in (
            await rows(
                eng,
                "SELECT checklist_snapshot s FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
                d=did,
            )
        )[0]["s"]
    ]
    for n in range(len(snap)):  # tick the first n: not enough
        await reset(eng, world, did, owner, "lead", ticked_all=False, ticked=tuple(snap[:n]))
        r = await client.post(
            f"/deals/{did}/move", json={"to_stage": "qualification"}, headers=H(owner)
        )
        assert r.status_code == 422 and r.json()["code"] == "criteria_incomplete"
        missing = {m["key"]: m["reason"] for m in r.json()["details"]["missing"]}
        assert set(missing) == set(snap[n:]) and set(missing.values()) == {"not ticked"}
    await reset(eng, world, did, owner, "lead", ticked_all=True)
    # one tick whose proof has gone ("Proof missing") also blocks the move
    await run(
        eng,
        "UPDATE deal_criteria SET proof_missing=true WHERE deal_id=:d AND criterion_key=:k",
        d=did,
        k=snap[0],
    )
    r = await client.post(
        f"/deals/{did}/move", json={"to_stage": "qualification"}, headers=H(owner)
    )
    assert r.status_code == 422
    assert r.json()["details"]["missing"] == [
        {
            "key": snap[0],
            "label": r.json()["details"]["missing"][0]["label"],
            "reason": "proof missing",
        }
    ]
    # nothing moved, no ok audit, no event
    assert await one(eng, "SELECT stage_key FROM deals WHERE id=:d", d=did) == "lead"


async def test_override_marks_exactly_the_unticked_criteria(client, world, admin_engine):
    eng = admin_engine
    did = await prep(client, world, eng)
    snap = [
        c["key"]
        for c in (
            await rows(
                eng,
                "SELECT checklist_snapshot s FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
                d=did,
            )
        )[0]["s"]
    ]
    for actor in (world.tl_north, world.admin, world.sa):
        await reset(eng, world, did, world.north1, "lead", ticked_all=False, ticked=(snap[0],))
        await run(
            eng,
            "UPDATE deal_criteria SET proof_missing=true WHERE deal_id=:d AND criterion_key=:k",
            d=did,
            k=snap[0],
        )
        mark = await max_seq(eng)
        r = await client.post(
            f"/deals/{did}/move",
            json={
                "to_stage": "qualification",
                "override": True,
                "reason": "Customer verbally confirmed",
            },
            headers=H(actor),
        )
        assert r.status_code == 200, r.text
        assert sorted(r.json()["overridden"]) == sorted(snap)  # the proof-missing one too
        last = (
            await rows(
                eng,
                "SELECT id FROM deal_stage_entries WHERE deal_id=:d AND stage_key='lead' ORDER BY entered_at DESC LIMIT 1",
                d=did,
            )
        )[0]["id"]
        marks = await rows(
            eng,
            "SELECT criterion_key, verify_status, verify_reason, verified_by FROM deal_criteria WHERE entry_id=:e",
            e=last,
        )
        assert {m["criterion_key"] for m in marks} == set(snap)
        assert all(
            m["verify_status"] == "overridden"
            and m["verify_reason"] == f"Overridden by {actor.name}"
            and m["verified_by"] == actor.id
            for m in marks
        )
        a = [x for x in await audit_rows(eng, did) if x["seq"] > mark]
        assert (
            len(a) == 1
            and a[0]["new_value"]["overridden"]
            and a[0]["new_value"]["kind"] == "override"
        )
    # the owner is told they were overridden, except when they did it themselves
    # (the deal page shows "Overridden by ...")
    det = (await client.get(f"/deals/{did}", headers=H(world.north1))).json()
    assert [v["exit_kind"] for v in det["visits"] if v["stage_key"] == "lead"][-1] == "override"


async def test_entry_checks_proposal_needs_a_document_and_revision_needs_feedback(
    client, world, admin_engine
):
    eng = admin_engine
    owner = world.north1
    did, _o = await mkdeal(client, world, eng)
    await reset(eng, world, did, owner, "prospecting")
    r = await client.post(f"/deals/{did}/move", json={"to_stage": "proposal"}, headers=H(owner))
    assert r.status_code == 422 and r.json()["code"] == "proof_required", r.text
    assert "proposal" in str(r.json()["details"]["missing"]).lower()
    await run(
        eng,
        "INSERT INTO documents (org_id, deal_id, kind, version_no, status, created_by) VALUES (:o,:d,'proposal',1,'draft',:u)",
        o=world.org_id,
        d=did,
        u=owner.id,
    )
    r = await client.post(f"/deals/{did}/move", json={"to_stage": "proposal"}, headers=H(owner))
    assert r.status_code == 200
    await reset(eng, world, did, owner, "proposal")
    r = await client.post(f"/deals/{did}/move", json={"to_stage": "revision"}, headers=H(owner))
    assert r.status_code == 422 and r.json()["code"] == "proof_required"
    assert "feedback" in str(r.json()["details"]["missing"]).lower()
    await set_data(eng, did, feedback="   ")
    r = await client.post(f"/deals/{did}/move", json={"to_stage": "revision"}, headers=H(owner))
    assert r.status_code == 422, "whitespace-only feedback must not count"
    await set_data(eng, did, feedback="Changes requested")
    r = await client.post(f"/deals/{did}/move", json={"to_stage": "revision"}, headers=H(owner))
    assert r.status_code == 200
    # a deleted proposal document does not count either
    await reset(eng, world, did, owner, "prospecting")
    await run(eng, "UPDATE documents SET deleted_at=now() WHERE deal_id=:d", d=did)
    r = await client.post(f"/deals/{did}/move", json={"to_stage": "proposal"}, headers=H(owner))
    assert r.status_code == 422
    # entry checks are for normal moves only: an Admin override walks past them (with a reason)
    r = await client.post(
        f"/deals/{did}/move",
        json={"to_stage": "proposal", "override": True, "reason": "doc is on paper"},
        headers=H(world.admin),
    )
    assert r.status_code == 200


# ------------------------------------------------------------------------------ Revision rounds
async def test_revision_is_mandatory_and_revision_2_counts_rounds(client, world, admin_engine):
    eng = admin_engine
    owner = world.north1
    did = await prep(client, world, eng)
    await reset(eng, world, did, owner, "proposal")
    # Proposal -> Closure is impossible for the owner (and for a normal move by anyone)
    r = await client.post(
        f"/deals/{did}/move", json={"to_stage": "closure", "reason": "x"}, headers=H(owner)
    )
    assert r.status_code == 403
    r = await client.post(
        f"/deals/{did}/move", json={"to_stage": "closure"}, headers=H(world.tl_north)
    )
    assert (
        r.status_code == 422 and r.json()["code"] == "reason_required"
    )  # a skip always needs a reason
    # Revision -> Revision 2: round 2, reason always required (also for the Super admin)
    await reset(eng, world, did, owner, "revision", ticked_all=False)
    for actor in (owner, world.tl_north, world.admin, world.sa):
        await set_owner(eng, did, actor)
        await reset(eng, world, did, actor, "revision", ticked_all=False)
        r = await client.post(
            f"/deals/{did}/move", json={"to_stage": "revision_2"}, headers=H(actor)
        )
        assert r.status_code == 422 and r.json()["code"] == "reason_required", actor.name
        r = await client.post(
            f"/deals/{did}/move", json={"to_stage": "revision_2", "reason": "   "}, headers=H(actor)
        )
        assert r.status_code == 422, "whitespace is no reason"
    await reset(eng, world, did, owner, "revision", ticked_all=False)
    seen = []
    for expected_round, target in ((2, "revision_2"), (3, "revision_2"), (4, "revision_2")):
        r = await client.post(
            f"/deals/{did}/move",
            json={"to_stage": target, "reason": f"Round {expected_round} changes"},
            headers=H(owner),
        )
        assert r.status_code == 200, r.text
        det = (await client.get(f"/deals/{did}", headers=H(owner))).json()
        seen.append(
            (
                det["stage_key"],
                await one(eng, "SELECT revision_round FROM deals WHERE id=:d", d=did),
            )
        )
        # the board shows one Revision column with the round badge
        board = (await client.get("/board", headers=H(owner))).json()
        col = next(c for c in board["columns"] if c["key"] == "revision")
        card = next(c for c in col["cards"] if c["id"] == did)
        assert card["revision_round"] == expected_round
    assert seen == [("revision_2", 2), ("revision_2", 3), ("revision_2", 4)]
    # every visit is recorded with its round and the reason
    ent = await rows(
        eng,
        "SELECT stage_key, revision_round, reason FROM deal_stage_entries WHERE deal_id=:d AND stage_key='revision_2' ORDER BY entered_at",
        d=did,
    )
    assert [(e["revision_round"]) for e in ent][-3:] == [2, 3, 4]
    # Revision 2 -> Closure once its criteria are ticked
    await reset(eng, world, did, owner, "revision_2", ticked_all=True, ticked=())
    await run(eng, "UPDATE deals SET revision_round=4 WHERE id=:d", d=did)
    r = await client.post(f"/deals/{did}/move", json={"to_stage": "closure"}, headers=H(owner))
    assert r.status_code == 200 and r.json()["kind"] == "forward"
    # board keeps only one Revision column and no Revision 2 column
    board = (await client.get("/board", headers=H(owner))).json()
    assert [c["key"] for c in board["columns"]] == [
        "lead",
        "qualification",
        "meeting_scheduled",
        "prospecting",
        "proposal",
        "revision",
        "closure",
    ]
    rev = next(c for c in board["columns"] if c["key"] == "revision")
    assert rev["stage_keys"] == ["revision", "revision_2"]


async def test_BUG_P3_09_skip_into_revision_2_keeps_round_one(client, world, admin_engine):
    eng = admin_engine
    did = await prep(client, world, eng)
    await reset(eng, world, did, world.north1, "proposal", ticked_all=False)
    r = await client.post(
        f"/deals/{did}/move",
        json={"to_stage": "revision_2", "reason": "customer asked for changes twice"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200, r.text
    assert await one(eng, "SELECT stage_key FROM deals WHERE id=:d", d=did) == "revision_2"
    assert await one(eng, "SELECT revision_round FROM deals WHERE id=:d", d=did) >= 2


async def test_backward_move_gives_a_fresh_checklist_and_old_ticks_do_not_carry(
    client, world, admin_engine
):
    eng = admin_engine
    owner = world.north1
    did = await prep(client, world, eng)
    await reset(eng, world, did, owner, "prospecting", ticked_all=True)
    r = await client.post(
        f"/deals/{did}/move", json={"to_stage": "qualification"}, headers=H(owner)
    )
    assert r.status_code == 422 and r.json()["code"] == "reason_required"
    r = await client.post(
        f"/deals/{did}/move",
        json={"to_stage": "qualification", "reason": "Budget fell through"},
        headers=H(owner),
    )
    assert r.status_code == 200 and r.json()["kind"] == "backward"
    det = (await client.get(f"/deals/{did}", headers=H(owner))).json()
    assert [c["ticked"] for c in det["progress"]["criteria"]] == [False, False]
    # and moving forward again is blocked until the criteria are ticked again
    r = await client.post(
        f"/deals/{did}/move", json={"to_stage": "meeting_scheduled"}, headers=H(owner)
    )
    assert r.status_code == 422 and r.json()["code"] == "criteria_incomplete"
    # the visit history shows the loop
    visits = [(v["stage_key"], v["exit_kind"]) for v in det["visits"]]
    assert visits[-2:] == [("prospecting", "backward"), ("qualification", None)]


async def test_expected_stage_and_version_guards(client, world, admin_engine):
    eng = admin_engine
    owner = world.north1
    did = await prep(client, world, eng)
    await reset(eng, world, did, owner, "lead")
    r = await client.post(
        f"/deals/{did}/move",
        json={"to_stage": "qualification", "expected_stage": "proposal"},
        headers=H(owner),
    )
    assert r.status_code == 409 and r.json()["details"]["current_stage"] == "lead"
    r = await client.post(
        f"/deals/{did}/move",
        json={"to_stage": "qualification", "expected_version": 9999},
        headers=H(owner),
    )
    assert r.status_code == 409
    v = (await client.get(f"/deals/{did}", headers=H(owner))).json()["version"]
    r = await client.post(
        f"/deals/{did}/move",
        json={"to_stage": "qualification", "expected_stage": "lead", "expected_version": v},
        headers=H(owner),
    )
    assert r.status_code == 200
    # unknown stage, junk fields
    r = await client.post(
        f"/deals/{did}/move", json={"to_stage": "nowhere", "reason": "x"}, headers=H(owner)
    )
    assert r.status_code == 422
    r = await client.post(
        f"/deals/{did}/move",
        json={"to_stage": "lead", "reason": "x", "status": "won"},
        headers=H(owner),
    )
    assert r.status_code == 422


async def test_bulk_stage_move_is_denied_to_everyone(client, world, admin_engine):
    eng = admin_engine
    did = await prep(client, world, eng)
    await reset(eng, world, did, world.north1, "lead")
    for actor in world.all_actors:
        for url in ("/deals/bulk/move", "/deals/bulk-move", "/deals/bulk/stage"):
            r = await client.post(
                url, json={"deal_ids": [did], "to_stage": "qualification"}, headers=H(actor)
            )
            assert r.status_code in (404, 405, 422), (url, r.status_code)
    assert await one(eng, "SELECT stage_key FROM deals WHERE id=:d", d=did) == "lead"
    keys = await rows(
        eng, "SELECT role, scope FROM role_permissions WHERE key='deal.bulk_stage_move'"
    )
    assert keys and all(k["scope"] == "none" for k in keys)


async def test_won_on_entering_onboarding_through_the_real_flow(client, world, admin_engine):
    from tests.helpers import dispatch_all
    from tests.modules.journey import move, run_to

    deal, owner = await run_to(client, world, admin_engine, "closure")
    did = deal["id"]
    from tests.modules.journey import add_doc, due, make_file, patch, tick

    fid = await make_file(admin_engine, world, did, owner)
    await add_doc(client, owner, did, "agreement", "countersigned", fid)
    await patch(client, owner, did, billing_contact="a@b.in", subscription_start_date=due(15), contract_term="12 months", billing_frequency="monthly", renewal_date=due(380), advance_amount_paise=50000, advance_received_by="Neha Kapoor", advance_received_contact="neha@acme.in", advance_received_on=due(0))  # fmt: skip
    await add_doc(client, owner, did, "advance", "received", await make_file(admin_engine, world, did, owner))
    for key in ("agreement_countersigned", "advance_received", "onboarding_info"):
        await tick(client, owner, did, key)
    mark, ev = await max_seq(admin_engine), await max_event(admin_engine)
    r = await move(client, owner, did, "onboarding")
    assert r.json()["won"] is True and r.json()["kind"] == "forward"
    row = (
        await rows(admin_engine, "SELECT status, won_at, stage_key FROM deals WHERE id=:d", d=did)
    )[0]
    assert row["status"] == "won" and row["won_at"] is not None and row["stage_key"] == "onboarding"
    types = sorted(e["type"] for e in await events_after(admin_engine, ev, did))
    assert types == ["deal.closed", "deal.moved", "deal.won"]
    # leaves the board, appears on the Won page and on the Onboarding page (BUG-P3-18)
    board = (await client.get("/board", headers=H(owner))).json()
    assert all(did != c["id"] for col in board["columns"] for c in col["cards"])
    won = (await client.get("/stages/won/deals", headers=H(owner))).json()
    assert did in [x["id"] for x in won["rows"]]
    onb = (await client.get("/stages/onboarding/deals", headers=H(owner))).json()
    assert did in [x["id"] for x in onb["rows"]]
    # onboarding criteria can still be ticked after Won; moves are closed
    det = (await client.get(f"/deals/{did}", headers=H(owner))).json()
    assert det["status"] == "won" and [c["key"] for c in det["progress"]["criteria"]] == [
        "prerequisite_document",
        "technical_document",
        "kickoff_done",
        "agent_live",
        "first_invoice",
    ]
    r = await client.post(
        f"/deals/{did}/move", json={"to_stage": "closure", "reason": "oops"}, headers=H(world.admin)
    )
    assert r.status_code == 409
    n = await dispatch_all()
    assert n >= 0
    notes = [
        n.type
        for n in await __import__("tests.helpers", fromlist=["x"]).notifications_for(
            world.tl_north.id
        )
    ]
    assert "deal.won" in notes  # the Team Leader hears about it
    a = [x for x in await audit_rows(admin_engine, did) if x["seq"] > mark]
    assert [x["action_key"] for x in a] == ["deal.move_forward"]


# --------------------------------------------------------------------------------- snapshots
async def test_pipeline_change_after_entry_does_not_touch_open_checklists(
    client, world, admin_engine
):
    from tests.deep.phase3.test_01_pipeline_config import get_v, publish, save, to_draft

    eng = admin_engine
    owner = world.north1
    did = await prep(client, world, eng)
    await reset(eng, world, did, owner, "qualification", ticked_all=False)
    before = await one(
        eng,
        "SELECT checklist_snapshot::text FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
        d=did,
    )
    d = to_draft(await get_v(client, world.admin, 1))
    d["criteria"] = [
        c for c in d["criteria"] if c["stage_key"] != "qualification"
    ]  # qualification has no criteria now
    no = (await save(client, world.admin, d)).json()["version_no"]
    await publish(client, world.admin, no)
    after = await one(
        eng,
        "SELECT checklist_snapshot::text FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
        d=did,
    )
    assert before == after
    # the old checklist still blocks the move, as it was when the deal entered
    r = await client.post(
        f"/deals/{did}/move", json={"to_stage": "meeting_scheduled"}, headers=H(owner)
    )
    assert r.status_code == 422 and len(r.json()["details"]["missing"]) == 2
    # a deal that goes back and re-enters the stage gets the new (empty) checklist
    await client.post(
        f"/deals/{did}/move", json={"to_stage": "lead", "reason": "recheck"}, headers=H(owner)
    )
    await force_stage(eng, world.org_id, did, "lead", all_ticked=True, ticker=owner)
    r = await client.post(
        f"/deals/{did}/move", json={"to_stage": "qualification"}, headers=H(owner)
    )
    assert r.status_code == 200
    snap = (
        await rows(
            eng,
            "SELECT checklist_snapshot s FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
            d=did,
        )
    )[0]["s"]
    assert snap == []
    r = await client.post(
        f"/deals/{did}/move", json={"to_stage": "meeting_scheduled"}, headers=H(owner)
    )
    assert r.status_code == 200  # nothing to tick in the new rules


async def test_reopen_enters_the_lost_from_stage_with_a_new_snapshot(client, world, admin_engine):
    from tests.deep.phase3.test_01_pipeline_config import get_v, publish, save, to_draft

    eng = admin_engine
    owner = world.north1
    did = await prep(client, world, eng)
    await reset(eng, world, did, owner, "qualification", ticked_all=False)
    old = await one(
        eng,
        "SELECT checklist_snapshot::text FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
        d=did,
    )
    r = await client.post(
        f"/deals/{did}/lose", json={"lost_reason": "Price", "notes": "too dear"}, headers=H(owner)
    )
    assert r.status_code == 200
    d = to_draft(await get_v(client, world.admin, 1))
    c = next(c for c in d["criteria"] if c["key"] == "need_documented")
    c["label"] = "Pain point written down (v2)"
    no = (await save(client, world.admin, d)).json()["version_no"]
    await publish(client, world.admin, no)
    r = await client.post(
        f"/deals/{did}/reopen", json={"reason": "Customer is back"}, headers=H(world.tl_north)
    )
    assert r.status_code == 200, r.text
    new = await one(
        eng,
        "SELECT checklist_snapshot::text FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
        d=did,
    )
    assert new != old and "(v2)" in new
    row = (
        await rows(
            eng,
            "SELECT status, stage_key, lost_at, lost_reason, lost_notes, lost_from_stage_key FROM deals WHERE id=:d",
            d=did,
        )
    )[0]
    assert row == {
        "status": "open",
        "stage_key": "qualification",
        "lost_at": None,
        "lost_reason": None,
        "lost_notes": None,
        "lost_from_stage_key": None,
    }
