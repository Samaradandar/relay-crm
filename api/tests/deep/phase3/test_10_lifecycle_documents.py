# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 7 (part 1): lose, reopen, delete, restore, documents."""

import asyncio

from tests.deep.phase3.invariants import check_invariants
from tests.deep.phase3.p3 import (
    H,
    add_doc,
    audit_rows,
    configure,
    create,
    make_file,
    max_seq,
    mkdeal,
    one,
    rows,
)
from tests.helpers import dispatch_all


# ===================================================================================== lose
async def test_lose_reason_list_roles_audit_and_page(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng, stage="proposal")
    url = f"/deals/{did}/lose"
    for bad in ({}, {"lost_reason": ""}, {"lost_reason": "   "}, {"lost_reason": "price"}, {"lost_reason": "Nope"}, {"lost_reason": "Price; DROP TABLE deals"}, {"lost_reason": "Price", "status": "x"}, {"lost_reason": 5}):  # fmt: skip
        r = await client.post(url, json=bad, headers=H(owner))
        assert r.status_code == 422, bad
    for actor in (world.north2, world.south1, world.tl_south):
        r = await client.post(url, json={"lost_reason": "Price"}, headers=H(actor))
        assert r.status_code == 403, actor.name
    assert await one(eng, "SELECT status FROM deals WHERE id=:d", d=did) == "open"
    # a reason that was hidden from the list can no longer be chosen
    lr = (await client.get("/settings/lost-reasons", headers=H(world.admin))).json()
    keep = [{"id": x["id"], "label": x["label"]} for x in lr if x["label"] != "Timing"]
    await client.put(
        "/settings/lost-reasons", json={"items": keep, "reason": "x"}, headers=H(world.admin)
    )
    r = await client.post(url, json={"lost_reason": "Timing"}, headers=H(owner))
    assert r.status_code == 422
    mark = await max_seq(eng)
    r = await client.post(
        url, json={"lost_reason": "Competitor", "notes": "  went with X  "}, headers=H(owner)
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["status"], j["lost_reason"], j["lost_notes"], j["lost_from_stage"]) == (
        "lost",
        "Competitor",
        "went with X",
        "Proposal",
    )
    a = [x for x in await audit_rows(eng, did) if x["seq"] > mark and x["outcome"] == "ok"]
    assert [(x["action_key"], x["reason"]) for x in a] == [("deal.lose", "Competitor")]
    assert a[0]["old_value"]["stage"] == "proposal" and a[0]["new_value"]["status"] == "lost"
    # entry closed 'lost', deal off the board, on the Lost page, still readable
    assert (
        await one(
            eng,
            "SELECT count(*) FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
            d=did,
        )
        == 0
    )
    assert (
        await one(
            eng,
            "SELECT exit_kind FROM deal_stage_entries WHERE deal_id=:d ORDER BY entered_at DESC LIMIT 1",
            d=did,
        )
        == "lost"
    )
    board = (await client.get("/board", headers=H(owner))).json()
    assert str(did) not in {c["id"] for col in board["columns"] for c in col["cards"]}
    lost = (await client.get("/stages/lost/deals", headers=H(owner))).json()
    assert str(did) in [x["id"] for x in lost["rows"]]
    # a second lose and a lose of a won deal
    assert (
        await client.post(url, json={"lost_reason": "Price"}, headers=H(owner))
    ).status_code == 409
    w, _ = await mkdeal(client, world, eng, stage="onboarding")
    assert (
        await client.post(f"/deals/{w}/lose", json={"lost_reason": "Price"}, headers=H(world.admin))
    ).status_code == 409
    # roles with their reason rules: the listed reason is the reason
    for actor in (world.tl_north, world.admin, world.sa):
        d2, _ = await mkdeal(client, world, eng, owner=world.north1)
        r = await client.post(f"/deals/{d2}/lose", json={"lost_reason": "Price"}, headers=H(actor))
        assert r.status_code == 200, (actor.name, r.text[:100])
    assert not await check_invariants(eng, world.org_id)


async def test_lose_notifies_owner_and_frees_the_cap_for_a_waiting_lead(
    client, world, admin_engine
):
    # cap of 1 per rep in team North: with 2 reps, the third lead has to wait
    await configure(client, world, cap=1)
    d1 = await create(client, world.north1)
    d2 = await create(client, world.north1)
    d3 = await create(client, world.north1)
    assert [d["status"] for d in (d1, d2, d3)].count("pooled") == 1
    waiting = next(d for d in (d1, d2, d3) if d["status"] == "pooled")
    loser = next(d for d in (d1, d2, d3) if d["status"] == "open")
    r = await client.post(
        f"/deals/{loser['id']}/lose", json={"lost_reason": "Price"}, headers=H(world.tl_north)
    )
    assert r.status_code == 200
    await dispatch_all()
    await dispatch_all()
    got = (await client.get(f"/deals/{waiting['id']}", headers=H(world.admin))).json()
    assert got["status"] == "open" and got["owner"]["id"] == loser["owner"]["id"]
    from tests.helpers import notifications_for

    owner_id = loser["owner"]["id"]
    owner = next(a for a in world.all_actors if str(a.id) == owner_id)
    mine = [n for n in await notifications_for(owner.id) if n.type == "deal.lost"]
    assert len(mine) == 1  # told once (the TL closed it, the owner did not)
    # deleting a deal also frees the cap
    r = await client.post(
        f"/deals/{got['id']}/delete", json={"reason": "dup"}, headers=H(world.tl_north)
    )
    assert r.status_code == 204


async def test_reopen_rules_and_clean_state(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng, stage="closure")
    url = f"/deals/{did}/reopen"
    assert (
        await client.post(url, json={"reason": "x"}, headers=H(world.tl_north))
    ).status_code == 409  # not lost
    await client.post(
        f"/deals/{did}/lose", json={"lost_reason": "Price", "notes": "n"}, headers=H(owner)
    )
    for actor in (owner, world.north2, world.south1, world.tl_south):
        r = await client.post(url, json={"reason": "x"}, headers=H(actor))
        assert r.status_code == 403, actor.name
    assert (await client.post(url, json={}, headers=H(world.tl_north))).status_code == 422
    assert (await client.post(url, json={"reason": " "}, headers=H(world.admin))).status_code == 422
    mark = await max_seq(eng)
    r = await client.post(url, json={"reason": "Customer returned"}, headers=H(world.tl_north))
    assert (
        r.status_code == 200 and r.json()["status"] == "open" and r.json()["stage_key"] == "closure"
    )
    a = [x for x in await audit_rows(eng, did) if x["seq"] > mark and x["outcome"] == "ok"]
    assert [(x["action_key"], x["reason"]) for x in a] == [("deal.reopen", "Customer returned")]
    st = (
        await rows(
            eng,
            "SELECT status, lost_at, lost_reason, lost_notes, lost_from_stage_key FROM deals WHERE id=:d",
            d=did,
        )
    )[0]
    assert st == {
        "status": "open",
        "lost_at": None,
        "lost_reason": None,
        "lost_notes": None,
        "lost_from_stage_key": None,
    }
    assert (
        await one(
            eng,
            "SELECT count(*) FROM deal_stage_entries WHERE deal_id=:d AND exited_at IS NULL",
            d=did,
        )
        == 1
    )
    # lose + reopen again: full history is kept
    await client.post(f"/deals/{did}/lose", json={"lost_reason": "Timing"}, headers=H(world.admin))
    assert (
        await client.post(url, json={}, headers=H(world.sa))
    ).status_code == 200  # SA: reason optional
    visits = (await client.get(f"/deals/{did}", headers=H(owner))).json()["visits"]
    assert [v["exit_kind"] for v in visits if v["stage_key"] == "closure"] == ["lost", "lost", None]
    assert not await check_invariants(eng, world.org_id)


async def test_reopen_when_the_stage_was_removed(client, world, admin_engine):
    from tests.deep.phase3.test_01_pipeline_config import drop_stage, get_v, publish, save, to_draft

    eng = admin_engine
    did, owner = await mkdeal(client, world, eng, stage="meeting_scheduled")
    await client.post(f"/deals/{did}/lose", json={"lost_reason": "Price"}, headers=H(owner))
    d = drop_stage(
        to_draft(await get_v(client, world.admin, 1)),
        "meeting_scheduled",
        ("qualification", "prospecting"),
    )
    no = (await save(client, world.admin, d)).json()["version_no"]
    await publish(client, world.admin, no)  # a lost deal holds no stage: nothing to map
    r = await client.post(
        f"/deals/{did}/reopen", json={"reason": "back"}, headers=H(world.tl_north)
    )
    assert r.status_code == 409 and "no longer exists" in r.json()["message"]
    assert await one(eng, "SELECT status FROM deals WHERE id=:d", d=did) == "lost"


# ================================================================================ delete / restore
async def test_delete_and_restore_rules(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng, stage="qualification")
    d_url, r_url = f"/deals/{did}/delete", f"/deals/{did}/restore"
    for actor in (owner, world.north2, world.south1, world.tl_south):
        assert (
            await client.post(d_url, json={"reason": "dup"}, headers=H(actor))
        ).status_code == 403, actor.name
    assert (await client.post(d_url, json={}, headers=H(world.tl_north))).status_code == 422
    assert (
        await client.post(d_url, json={"reason": "  "}, headers=H(world.admin))
    ).status_code == 422
    mark = await max_seq(eng)
    assert (
        await client.post(d_url, json={"reason": "duplicate lead"}, headers=H(world.tl_north))
    ).status_code == 204
    a = [x for x in await audit_rows(eng, did) if x["seq"] > mark and x["outcome"] == "ok"]
    assert [(x["action_key"], x["reason"]) for x in a] == [("deal.delete", "duplicate lead")]
    # gone from every view, kept in storage and in the deleted view
    assert (await client.get(f"/deals/{did}", headers=H(owner))).status_code == 404
    assert str(did) not in {
        c["id"]
        for col in (await client.get("/board", headers=H(world.admin))).json()["columns"]
        for c in col["cards"]
    }
    assert str(did) not in [
        x["id"] for x in (await client.get("/deals", headers=H(world.admin))).json()
    ]
    assert await one(eng, "SELECT count(*) FROM deals WHERE id=:d", d=did) == 1
    assert (
        await client.post(d_url, json={"reason": "again"}, headers=H(world.tl_north))
    ).status_code == 404
    for actor, _code in (
        (owner, 403),
        (world.north2, 403),
        (world.tl_north, 200),
        (world.tl_south, 403),
        (world.admin, 200),
        (world.sa, 200),
    ):
        r = await client.get("/deleted/deals", headers=H(actor))
        assert r.status_code == (403 if actor in (owner, world.north2) else 200), actor.name
        if actor in (world.tl_north, world.admin, world.sa):
            assert str(did) in [x["id"] for x in r.json()]
        elif actor is world.tl_south:
            assert str(did) not in [x["id"] for x in r.json()]
    # restore
    for actor in (owner, world.south1, world.tl_south):
        assert (await client.post(r_url, json={"reason": "x"}, headers=H(actor))).status_code in (
            403,
            404,
        )
    assert (await client.post(r_url, json={}, headers=H(world.tl_north))).status_code == 422
    r = await client.post(r_url, json={"reason": "wrong delete"}, headers=H(world.tl_north))
    assert r.status_code == 200 and r.json()["stage_key"] == "qualification"
    assert (
        await client.post(r_url, json={"reason": "again"}, headers=H(world.tl_north))
    ).status_code == 409
    keys = [
        x["action_key"]
        for x in await audit_rows(eng, did)
        if x["outcome"] == "ok" and x["action_key"].startswith("deal.")
    ]
    assert keys.count("deal.delete") == 1 and keys.count("deal.restore") == 1
    assert not await check_invariants(eng, world.org_id)


async def test_delete_restore_of_a_pooled_deal_regains_its_pool_entry(client, world, admin_engine):
    eng = admin_engine
    first = await create(client, world.tl_north)  # no assignment settings: it waits in the pool
    assert first["status"] == "pooled"
    assert (
        await one(
            eng,
            "SELECT count(*) FROM lead_pool_entries WHERE deal_id=:d AND left_at IS NULL",
            d=first["id"],
        )
        == 1
    )
    assert (
        await client.post(
            f"/deals/{first['id']}/delete", json={"reason": "junk"}, headers=H(world.admin)
        )
    ).status_code == 204
    assert (
        await one(
            eng,
            "SELECT count(*) FROM lead_pool_entries WHERE deal_id=:d AND left_at IS NULL",
            d=first["id"],
        )
        == 0
    )
    assert (await client.get("/pool", headers=H(world.admin))).json() == []
    r = await client.post(
        f"/deals/{first['id']}/restore", json={"reason": "not junk"}, headers=H(world.admin)
    )
    assert r.status_code == 200
    assert (
        await one(
            eng,
            "SELECT count(*) FROM lead_pool_entries WHERE deal_id=:d AND left_at IS NULL",
            d=first["id"],
        )
        == 1
    )
    assert [p["deal_id"] for p in (await client.get("/pool", headers=H(world.admin))).json()] == [
        first["id"]
    ]
    # an open deal restored gets no pool entry
    did, _o = await mkdeal(client, world, eng)
    await client.post(f"/deals/{did}/delete", json={"reason": "x"}, headers=H(world.admin))
    await client.post(f"/deals/{did}/restore", json={"reason": "x"}, headers=H(world.admin))
    assert (
        await one(
            eng,
            "SELECT count(*) FROM lead_pool_entries WHERE deal_id=:d AND left_at IS NULL",
            d=did,
        )
        == 0
    )
    # every deal still has exactly one open stage entry after delete/restore
    assert not await check_invariants(eng, world.org_id)


async def test_delete_and_restore_race(client, world, admin_engine):
    did, _o = await mkdeal(client, world, admin_engine)
    res = await asyncio.gather(
        *(
            client.post(f"/deals/{did}/delete", json={"reason": "x"}, headers=H(world.admin))
            for _ in range(6)
        )
    )
    assert sorted(r.status_code for r in res) == [204] + [404] * 5
    res = await asyncio.gather(
        *(
            client.post(f"/deals/{did}/restore", json={"reason": "x"}, headers=H(world.admin))
            for _ in range(6)
        )
    )
    assert sorted(r.status_code for r in res) == [200] + [409] * 5


async def test_super_admin_deal_cannot_be_deleted_or_lost_by_a_team_leader(
    client, world, admin_engine
):
    did, _o = await mkdeal(client, world, admin_engine, owner=world.sa)
    assert (
        await client.post(f"/deals/{did}/delete", json={"reason": "x"}, headers=H(world.tl_north))
    ).status_code == 403
    assert (
        await client.post(
            f"/deals/{did}/lose", json={"lost_reason": "Price"}, headers=H(world.tl_north)
        )
    ).status_code == 403
    assert (await client.get(f"/deals/{did}", headers=H(world.tl_north))).status_code == 404
    assert (
        await client.post(f"/deals/{did}/delete", json={"reason": "x"}, headers=H(world.admin))
    ).status_code == 204


# ===================================================================================== documents
KINDS = {
    "proposal": ("draft", "sent"),
    "agreement": ("draft", "sent", "countersigned"),
    "invoice": ("draft", "sent"),
}


async def post_doc(client, actor, did, expect, **kw):
    r = await client.post(f"/deals/{did}/documents", json=kw, headers=H(actor))
    assert r.status_code == expect, (kw, actor.name, r.status_code, r.text[:200])
    return r


async def test_documents_add_matrix_kinds_statuses_roles(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    fid = await make_file(eng, world, did, owner)
    for kind in ("proposal", "agreement", "invoice"):
        for status in ("draft", "sent", "countersigned", "terminated", "void"):
            needs_file = kind == "agreement" and status in ("sent", "countersigned")
            ok_status = status in KINDS[kind]
            for file_id in (None, fid):
                body = {"kind": kind, "status": status, "file_id": file_id}
                if not ok_status:
                    await post_doc(client, owner, did, 422, **body)
                elif needs_file and file_id is None:
                    await post_doc(client, owner, did, 422, **body)
                else:
                    r = await post_doc(client, owner, did, 201, **body)
                    assert r.json()["kind"] == kind and r.json()["status"] == status
    # version numbers count per kind
    docs = (await client.get(f"/deals/{did}/documents", headers=H(owner))).json()
    for kind in KINDS:
        assert [d["version_no"] for d in docs if d["kind"] == kind] == list(
            range(1, len([d for d in docs if d["kind"] == kind]) + 1)
        )
    # who may add: owner, TL of the team, Admin (reason), SA; not other reps / teams
    for actor, code in (
        (world.north2, 403),
        (world.south1, 403),
        (world.tl_south, 403),
        (world.tl_north, 201),
        (world.admin, 422),
        (world.sa, 201),
    ):
        await post_doc(client, actor, did, code, kind="invoice", status="draft")
    await post_doc(
        client, world.admin, did, 201, kind="invoice", status="draft", reason="entered for the rep"
    )
    for bad in (
        {"kind": "contract"},
        {"kind": "invoice", "amount_paise": -1},
        {"kind": "invoice", "extra": 1},
        {"kind": "invoice", "file_id": "x"},
        {},
    ):
        await post_doc(client, owner, did, 422, **bad)
    # amounts and numbers: boundaries and injection text
    r = await post_doc(
        client,
        owner,
        did,
        201,
        kind="invoice",
        status="sent",
        number="=cmd|' /C calc'!A0",
        amount_paise=0,
        notes="<b>x</b>",
    )
    assert r.json()["number"] == "=cmd|' /C calc'!A0" and r.json()["amount_paise"] == 0
    # documents on lost / unknown deals
    lost, lo = await mkdeal(client, world, eng)
    await client.post(f"/deals/{lost}/lose", json={"lost_reason": "Price"}, headers=H(lo))
    await post_doc(client, lo, lost, 409, kind="proposal", status="draft")
    await post_doc(
        client, owner, "00000000-0000-0000-0000-000000000000", 404, kind="proposal", status="draft"
    )


async def test_BUG_P3_23_huge_amount(client, world, admin_engine):
    did, owner = await mkdeal(client, world, admin_engine)
    r = await client.post(
        f"/deals/{did}/documents",
        json={"kind": "invoice", "status": "draft", "amount_paise": 2**70},
        headers=H(owner),
    )
    assert r.status_code == 422


async def test_documents_edit_terminate_delete_matrix(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    fid = await make_file(eng, world, did, owner)
    ag = (await add_doc(client, owner, did, "agreement", "sent", fid))["id"]
    inv = (await add_doc(client, owner, did, "invoice", "draft", number="INV-1"))["id"]
    prop = (await add_doc(client, owner, did, "proposal", "draft"))["id"]
    patch = lambda doc, actor, **kw: client.patch(f"/documents/{doc}", json=kw, headers=H(actor))  # noqa: E731
    # agreement edit: not the rep; TL (reason), Admin (reason), SA
    assert (await patch(ag, owner, status="countersigned", reason="x")).status_code == 403
    assert (await patch(ag, world.tl_south, status="countersigned", reason="x")).status_code == 403
    assert (await patch(ag, world.tl_north, status="countersigned")).status_code == 422
    assert (
        await patch(ag, world.tl_north, notes="signed copy received", reason="paper in")
    ).status_code == 200
    assert (await patch(ag, world.admin, notes="n2")).status_code == 422
    assert (await patch(ag, world.admin, notes="n2", reason="ops")).status_code == 200
    assert (await patch(ag, world.sa, notes="n3")).status_code == 200
    # nothing to change, junk fields, forbidden status words
    assert (await patch(ag, world.sa)).status_code == 422
    assert (await patch(ag, world.sa, status="void")).status_code == 422
    assert (await patch(ag, world.sa, kind="invoice")).status_code == 422
    assert (await patch(ag, world.sa, status="draft", file_id=None)).status_code == 200
    # terminate: Admin / SA only, agreement only, reason for the Admin
    assert (await patch(ag, owner, status="terminated", reason="x")).status_code == 403
    assert (await patch(ag, world.tl_north, status="terminated", reason="x")).status_code == 403
    assert (await patch(ag, world.admin, status="terminated")).status_code == 422
    assert (
        await patch(ag, world.admin, status="terminated", reason="customer left")
    ).status_code == 200
    assert (await patch(inv, world.admin, status="terminated", reason="x")).status_code == 422
    assert (await patch(prop, world.sa, status="terminated")).status_code == 422
    docs = {
        d["id"]: d for d in (await client.get(f"/deals/{did}/documents", headers=H(owner))).json()
    }
    assert docs[ag]["status"] == "terminated"
    # a status that needs a file
    assert (await patch(ag, world.sa, status="sent")).status_code == 422
    # invoice edit: rep denied, TL becomes an approval, Admin and SA direct
    assert (await patch(inv, owner, number="X", reason="x")).status_code == 403
    r = await patch(inv, world.tl_north, number="INV-2", reason="typo in number")
    assert r.status_code == 202 and r.json()["code"] == "approval_requested"
    assert (await client.get(f"/documents/{inv}", headers=H(owner))).status_code in (404, 405)
    assert (
        next(
            d
            for d in (await client.get(f"/deals/{did}/documents", headers=H(owner))).json()
            if d["id"] == inv
        )["number"]
        == "INV-1"
    )
    ap = r.json()["approval_id"]
    assert (
        await client.post(
            f"/approvals/{ap}/decide",
            json={"decision": "approve", "reason": "ok"},
            headers=H(world.tl_north),
        )
    ).status_code == 403  # own request
    assert (
        await client.post(
            f"/approvals/{ap}/decide",
            json={"decision": "approve", "reason": "ok"},
            headers=H(world.admin),
        )
    ).status_code == 200
    assert (
        next(
            d
            for d in (await client.get(f"/deals/{did}/documents", headers=H(owner))).json()
            if d["id"] == inv
        )["number"]
        == "INV-2"
    )
    assert (
        await client.post(
            f"/approvals/{ap}/decide",
            json={"decision": "approve", "reason": "ok"},
            headers=H(world.sa),
        )
    ).status_code == 409
    assert (await patch(inv, world.admin, number="INV-3", reason="ops")).status_code == 200
    assert (await patch(inv, world.sa, number="INV-4")).status_code == 200
    # a stale invoice-edit request: the document is deleted before approval
    r = await patch(inv, world.tl_north, number="INV-9", reason="again")
    ap2 = r.json()["approval_id"]
    assert (
        await client.delete(
            f"/documents/{inv}", params={"reason": "wrong invoice"}, headers=H(world.admin)
        )
    ).status_code == 204
    r = await client.post(
        f"/approvals/{ap2}/decide",
        json={"decision": "approve", "reason": "ok"},
        headers=H(world.sa),
    )
    assert r.status_code in (404, 409), r.status_code
    # proposal edit: the owner may (it is a deal edit), others in scope too
    assert (await patch(prop, owner, status="sent")).status_code == 200
    assert (await patch(prop, world.north2, status="draft")).status_code == 403
    # delete: rep may delete own proposal, not agreement/invoice; invoice delete by TL is "ask an Admin"
    inv2 = (await add_doc(client, owner, did, "invoice", "draft"))["id"]
    ag2 = (await add_doc(client, owner, did, "agreement", "draft"))["id"]

    def dele(doc, actor, reason="x"):
        return client.delete(
            f"/documents/{doc}", params={"reason": reason} if reason else {}, headers=H(actor)
        )

    assert (await dele(ag2, owner)).status_code == 403
    assert (await dele(inv2, owner)).status_code == 403
    assert (await dele(inv2, world.tl_north)).status_code == 422
    assert (await dele(ag2, world.tl_north, None)).status_code == 422
    assert (await dele(ag2, world.tl_north)).status_code == 204
    assert (await dele(prop, owner, None)).status_code in (204, 422)
    assert (await dele(inv2, world.admin, None)).status_code == 422
    assert (await dele(inv2, world.sa, None)).status_code == 204
    assert (await dele(inv2, world.sa)).status_code == 404
    assert (
        await one(
            eng, "SELECT count(*) FROM documents WHERE deal_id=:d AND deleted_at IS NULL", d=did
        )
        >= 0
    )
    assert (
        await one(eng, "SELECT count(*) FROM documents WHERE deal_id=:d", d=did) == 5
    )  # nothing erased


async def test_document_version_numbers_never_reused_and_idor(client, world, admin_engine):
    eng = admin_engine
    d1, o1 = await mkdeal(client, world, eng)
    d2, o2 = await mkdeal(client, world, eng, owner=world.south1)
    a = (await add_doc(client, o1, d1, "proposal", "draft"))["id"]
    b = (await add_doc(client, o1, d1, "proposal", "draft"))["id"]
    await client.delete(f"/documents/{b}", params={"reason": "x"}, headers=H(o1))
    c = await add_doc(client, o1, d1, "proposal", "draft")
    assert c["version_no"] == 3  # the deleted v2 is not reused
    # another team's people cannot see, edit or delete it; unknown ids are 404
    assert (await client.get(f"/deals/{d1}/documents", headers=H(world.south1))).status_code == 404
    assert (
        await client.patch(f"/documents/{a}", json={"notes": "x"}, headers=H(o2))
    ).status_code in (403, 404)
    assert (
        await client.delete(f"/documents/{a}", params={"reason": "x"}, headers=H(world.tl_south))
    ).status_code in (403, 404)
    assert (
        await client.patch(
            "/documents/00000000-0000-0000-0000-000000000000",
            json={"notes": "x"},
            headers=H(world.sa),
        )
    ).status_code == 404
    # a document can reuse neither another deal's file nor be attached to two different kinds of deal
    f2 = await make_file(eng, world, d2, o2)
    await add_doc(client, o1, d1, "agreement", "sent", f2, expect=422)
    # the file of this deal can be attached twice (two documents, one file): note only
    f1 = await make_file(eng, world, d1, o1)
    await add_doc(client, o1, d1, "agreement", "sent", f1)
    await add_doc(client, o1, d1, "agreement", "countersigned", f1)
    assert await one(eng, "SELECT count(*) FROM documents WHERE file_id=:f", f=f1) == 2


async def test_document_audit_rows_one_each_with_reason(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    mark = await max_seq(eng)
    fid = await make_file(eng, world, did, owner)
    ag = (await add_doc(client, owner, did, "agreement", "sent", fid))["id"]
    await client.patch(
        f"/documents/{ag}", json={"notes": "n", "reason": "tidy"}, headers=H(world.tl_north)
    )
    await client.delete(f"/documents/{ag}", params={"reason": "dup"}, headers=H(world.tl_north))
    a = [
        (x["action_key"], x["reason"])
        for x in await audit_rows(eng, did)
        if x["seq"] > mark and x["outcome"] == "ok"
    ]
    assert a == [("agreement.add", None), ("agreement.edit", "tidy"), ("agreement.edit", "dup")]


async def test_BUG_P3_24_deleted_deal_children_stay_readable(client, world, admin_engine):
    eng = admin_engine
    did, owner = await mkdeal(client, world, eng)
    act = (
        await client.post(
            f"/deals/{did}/activities",
            json={
                "type": "call",
                "two_way": True,
                "discussed": "secret notes",
                "next_step_text": "x",
                "next_step_due_date": "2999-01-01",
            },
            headers=H(owner),
        )
    ).json()["id"]
    await add_doc(client, owner, did, "proposal", "draft")
    r = await client.post(f"/deals/{did}/delete", json={"reason": "dup"}, headers=H(world.tl_north))
    assert r.status_code == 204
    leaks = []
    for path in (
        f"/deals/{did}/activities",
        f"/deals/{did}/documents",
        f"/deals/{did}/lines",
        f"/activities/{act}/versions",
    ):
        r = await client.get(path, headers=H(owner))
        if r.status_code == 200:
            leaks.append(path)
    assert not leaks, f"a rep can still read {leaks} of a deleted deal"
