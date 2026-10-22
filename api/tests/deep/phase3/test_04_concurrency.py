# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 2 (concurrency): races on one deal and many deals at once."""

import asyncio

from tests.deep.phase3.invariants import check_invariants
from tests.deep.phase3.p3 import (
    H,
    Rig,
    audit_rows,
    configure,
    create,
    force_stage,
    max_seq,
    mkdeal,
    one,
    rows,
    run,
    set_owner,
)


def ok(res, *, allow=(200, 201, 202, 204, 409, 404, 422, 403)):
    bad = [r for r in res if r.status_code not in allow or r.status_code >= 500]
    assert not bad, [(r.status_code, r.text[:200]) for r in bad]


async def test_same_move_ten_times_one_winner(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine, all_ticked=True)
    mark = await max_seq(admin_engine)
    res = await asyncio.gather(
        *(
            client.post(f"/deals/{did}/move", json={"to_stage": "qualification"}, headers=H(owner))
            for _ in range(10)
        )
    )
    ok(res)
    assert sorted(r.status_code for r in res).count(200) == 1
    a = [x for x in await audit_rows(admin_engine, did) if x["seq"] > mark and x["outcome"] == "ok"]
    assert len(a) == 1
    assert (
        await one(
            admin_engine,
            "SELECT count(*) FROM deal_stage_entries WHERE deal_id=:d AND stage_key='qualification'",
            d=did,
        )
        == 1
    )
    assert not await check_invariants(admin_engine, world.org_id)


async def test_expected_stage_guard_makes_the_loser_a_clean_409(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine, all_ticked=True)
    body = {"to_stage": "qualification", "expected_stage": "lead"}
    res = await asyncio.gather(
        *(client.post(f"/deals/{did}/move", json=body, headers=H(owner)) for _ in range(6))
    )
    assert sorted(r.status_code for r in res) == [200] + [409] * 5


async def test_different_targets_at_once(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine, all_ticked=True)
    res = await asyncio.gather(
        client.post(f"/deals/{did}/move", json={"to_stage": "qualification"}, headers=H(owner)),
        client.post(
            f"/deals/{did}/move",
            json={"to_stage": "meeting_scheduled", "reason": "skip", "override": True},
            headers=H(world.tl_north),
        ),
        client.post(
            f"/deals/{did}/move",
            json={"to_stage": "prospecting", "reason": "skip"},
            headers=H(world.admin),
        ),
        client.post(
            f"/deals/{did}/move",
            json={"to_stage": "qualification", "reason": "again"},
            headers=H(world.sa),
        ),
    )
    ok(res)
    assert 200 in [r.status_code for r in res]
    stage = await one(admin_engine, "SELECT stage_key FROM deals WHERE id=:d", d=did)
    n_ok = await one(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE entity_id=:d AND outcome='ok' AND action_key LIKE 'deal.move%' OR entity_id=:d AND outcome='ok' AND action_key='deal.override_or_skip'",
        d=str(did),
    )
    assert n_ok == [r.status_code for r in res].count(200)  # one ok audit row per successful move
    assert stage != "lead"
    assert not await check_invariants(admin_engine, world.org_id)


async def test_tick_vs_reject_vs_untick(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    await rig.log("call")
    await rig.tick("contact_verified", 204)
    vurl = f"/deals/{rig.did}/criteria/contact_verified/verify"
    for _ in range(3):
        res = await asyncio.gather(
            client.post(
                vurl, json={"decision": "reject", "reason": "no"}, headers=H(world.tl_north)
            ),
            client.post(
                f"/deals/{rig.did}/criteria/contact_verified/untick",
                json={"reason": "no"},
                headers=H(rig.owner),
            ),
            client.post(
                f"/deals/{rig.did}/criteria/contact_verified/tick", json={}, headers=H(rig.owner)
            ),
        )
        ok(res)
        n = await one(
            admin_engine, "SELECT count(*) FROM deal_criteria WHERE deal_id=:d", d=rig.did
        )
        assert n in (0, 1)
        if n == 0:
            await rig.tick("contact_verified", 204)
    # tick racing a tick: exactly one row
    await run(admin_engine, "DELETE FROM deal_criteria WHERE deal_id=:d", d=rig.did)
    res = await asyncio.gather(*(rig.tick("contact_verified", None) for _ in range(10)))
    assert sorted(r.status_code for r in res) == [204] + [409] * 9
    assert not await check_invariants(admin_engine, world.org_id)


async def test_move_vs_edit_vs_activity_delete(client, world, admin_engine):
    rig = await Rig(client, world, admin_engine).start("lead", prove_before=False)
    a = (await rig.log("call"))["id"]
    for k in ("basic_info", "company_identified", "contact_verified"):
        await rig.tick(k, 204)
    res = await asyncio.gather(
        client.post(
            f"/deals/{rig.did}/move", json={"to_stage": "qualification"}, headers=H(rig.owner)
        ),
        client.patch(f"/deals/{rig.did}", json={"use_case": "x"}, headers=H(rig.owner)),
        client.delete(f"/activities/{a}", params={"reason": "dup"}, headers=H(world.tl_north)),
        client.patch(
            f"/deals/{rig.did}", json={"lead_source": "Referral"}, headers=H(world.tl_north)
        ),
    )
    ok(res)
    assert not await check_invariants(admin_engine, world.org_id)


async def test_move_vs_lose_vs_delete(client, world, admin_engine):
    for variant in ("lose", "delete", "bulk"):
        did, owner = await mkdeal(client, world, admin_engine, all_ticked=True)
        calls = [
            client.post(f"/deals/{did}/move", json={"to_stage": "qualification"}, headers=H(owner))
        ]
        if variant == "lose":
            calls.append(
                client.post(
                    f"/deals/{did}/lose", json={"lost_reason": "Price"}, headers=H(world.tl_north)
                )
            )
        elif variant == "delete":
            calls.append(
                client.post(
                    f"/deals/{did}/delete", json={"reason": "dup"}, headers=H(world.tl_north)
                )
            )
        else:
            calls.append(
                client.post(
                    "/deals/bulk/lose",
                    json={"deal_ids": [did], "lost_reason": "Price", "reason": "x"},
                    headers=H(world.tl_north),
                )
            )
        res = await asyncio.gather(*calls)
        ok(res)
        st = (
            await rows(
                admin_engine,
                "SELECT status, stage_key, lost_from_stage_key, deleted_at FROM deals WHERE id=:d",
                d=did,
            )
        )[0]
        if variant == "lose":
            moved = res[0].status_code == 200
            if st["status"] == "lost":
                assert st["lost_from_stage_key"] == ("qualification" if moved else "lead")
        assert not await check_invariants(admin_engine, world.org_id)
    # double lose and double reopen
    did, owner = await mkdeal(client, world, admin_engine)
    res = await asyncio.gather(
        *(
            client.post(f"/deals/{did}/lose", json={"lost_reason": "Price"}, headers=H(owner))
            for _ in range(5)
        )
    )
    assert sorted(r.status_code for r in res) == [200] + [409] * 4
    res = await asyncio.gather(
        *(
            client.post(f"/deals/{did}/reopen", json={"reason": "back"}, headers=H(world.tl_north))
            for _ in range(5)
        )
    )
    assert sorted(r.status_code for r in res) == [200] + [409] * 4
    assert not await check_invariants(admin_engine, world.org_id)


async def test_fifty_deals_moved_at_once_by_different_owners(client, world, admin_engine):
    await configure(client, world, cap=100)
    reps = [world.north1, world.north2, world.south1, world.south2]
    ids = []
    for i in range(50):
        d = await create(client, reps[i % 4])
        owner = reps[i % 4]
        await set_owner(admin_engine, d["id"], owner)
        await force_stage(
            admin_engine, world.org_id, d["id"], "lead", all_ticked=True, ticker=owner
        )
        ids.append((d["id"], owner))
    mark = await max_seq(admin_engine)
    res = await asyncio.gather(
        *(
            client.post(f"/deals/{did}/move", json={"to_stage": "qualification"}, headers=H(o))
            for did, o in ids
        )
    )
    assert [r.status_code for r in res] == [200] * 50, [
        r.text[:100] for r in res if r.status_code != 200
    ]
    assert (
        await one(
            admin_engine,
            "SELECT count(*) FROM audit_log WHERE seq>:m AND action_key='deal.move_forward' AND outcome='ok'",
            m=mark,
        )
        == 50
    )
    assert (
        await one(
            admin_engine,
            "SELECT count(*) FROM deals WHERE org_id=:o AND stage_key='qualification'",
            o=world.org_id,
        )
        == 50
    )
    assert not await check_invariants(admin_engine, world.org_id)


async def test_mixed_storm_on_one_deal_keeps_the_invariants(client, world, admin_engine):
    import random

    rng = random.Random(7)
    rig = await Rig(client, world, admin_engine).start("qualification")
    d = rig.did
    actors = [rig.owner, world.tl_north, world.admin, world.sa, world.north2, world.tl_south]
    calls = []
    for _ in range(60):
        a = rng.choice(actors)
        kind = rng.choice(
            ["tick", "untick", "move", "back", "patch", "lose", "note", "skip", "verify"]
        )
        h = H(a)
        if kind == "tick":
            calls.append(
                client.post(
                    f"/deals/{d}/criteria/{rng.choice(['need_documented', 'fits_profile', 'meeting_scheduled'])}/tick",
                    json={},
                    headers=h,
                )
            )
        elif kind == "untick":
            calls.append(
                client.post(
                    f"/deals/{d}/criteria/need_documented/untick", json={"reason": "r"}, headers=h
                )
            )
        elif kind == "move":
            calls.append(
                client.post(f"/deals/{d}/move", json={"to_stage": "meeting_scheduled"}, headers=h)
            )
        elif kind == "back":
            calls.append(
                client.post(f"/deals/{d}/move", json={"to_stage": "lead", "reason": "r"}, headers=h)
            )
        elif kind == "skip":
            calls.append(
                client.post(
                    f"/deals/{d}/move", json={"to_stage": "prospecting", "reason": "r"}, headers=h
                )
            )
        elif kind == "patch":
            calls.append(
                client.patch(
                    f"/deals/{d}",
                    json={"pain_point": rng.choice(["a", None, "b"]), "reason": "r"},
                    headers=h,
                )
            )
        elif kind == "lose":
            calls.append(client.post(f"/deals/{d}/lose", json={"lost_reason": "Price"}, headers=h))
        elif kind == "verify":
            calls.append(
                client.post(
                    f"/deals/{d}/criteria/need_documented/verify",
                    json={"decision": rng.choice(["verify", "reject"]), "reason": "r"},
                    headers=h,
                )
            )
        else:
            calls.append(
                client.post(
                    f"/deals/{d}/activities",
                    json={
                        "type": "note",
                        "discussed": "n",
                        "next_step_text": "x",
                        "next_step_due_date": "2999-01-01",
                    },
                    headers=h,
                )
            )
    res = await asyncio.gather(*calls)
    ok(res)
    assert not await check_invariants(admin_engine, world.org_id)
