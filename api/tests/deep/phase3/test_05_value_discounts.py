# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 deep tests, section 4 (first half): deal value rule, discount rule, product lines,
approvals. Boundary heavy. Expected tables are from PRD 4.4 B and G and the Phase 3 prompt."""

import asyncio
from decimal import ROUND_HALF_UP, Decimal

import pytest

from tests.deep.phase3.invariants import check_invariants
from tests.deep.phase3.p3 import H, audit_rows, max_seq, mkdeal, one, run, set_owner


async def product(client, world, name, list_p, floor, unit="fixed", actor=None, **kw):
    r = await client.put(
        "/products",
        json={
            "products": [
                {
                    "name": name,
                    "unit": unit,
                    "list_price_paise": list_p,
                    "floor_price_paise": floor,
                    **kw,
                }
            ],
            "reason": "qa",
        },
        headers=H(actor or world.admin),
    )
    assert r.status_code == 200, r.text
    return next(p for p in r.json() if p["name"] == name)


async def add_line(client, actor, did, pid, qty, expect=201, **kw):
    r = await client.post(
        f"/deals/{did}/lines", json={"product_id": pid, "quantity": qty, **kw}, headers=H(actor)
    )
    assert r.status_code == expect, (r.status_code, r.text[:300])
    return r


async def patch_line(client, actor, did, lid, expect=200, **kw):
    r = await client.patch(f"/deals/{did}/lines/{lid}", json=kw, headers=H(actor))
    assert r.status_code == expect, (r.status_code, r.text[:300])
    return r


async def value(eng, did):
    return await one(eng, "SELECT value_paise FROM deals WHERE id=:d", d=did)


async def lines_of(client, actor, did):
    return (await client.get(f"/deals/{did}/lines", headers=H(actor))).json()


def role_setup(world):
    # (label, actor, deal owner)
    return [
        ("user_owner", world.north1, world.north1),
        ("tl_owner", world.tl_north, world.tl_north),
        ("tl_team_deal", world.tl_north, world.north1),
        ("admin", world.admin, world.north1),
        ("sa", world.sa, world.north1),
    ]


def expected_value_outcome(label, pct_abs: Decimal):
    """(status, needs reason) for a quantity change of `pct_abs` percent, PRD 4.4 B."""
    if pct_abs <= 5:
        return (200, label == "admin")
    if pct_abs <= 10:
        return (200, label != "sa")
    if label in ("user_owner", "tl_owner", "tl_team_deal"):
        return (202, True)  # becomes a request; the request itself always needs a reason
    return (200, label == "admin")


BASE_QTY = 100000  # product at 1 paise: value 100,000 paise
CASES = [  # new quantity, percent change (absolute)
    (104990, Decimal("4.99")),
    (105000, Decimal("5.00")),
    (105010, Decimal("5.01")),
    (109990, Decimal("9.99")),
    (110000, Decimal("10.00")),
    (110010, Decimal("10.01")),
    (95010, Decimal("4.99")),
    (95000, Decimal("5.00")),
    (94990, Decimal("5.01")),
    (90010, Decimal("9.99")),
    (90000, Decimal("10.00")),
    (89990, Decimal("10.01")),
]


@pytest.mark.parametrize("label", ["user_owner", "tl_owner", "tl_team_deal", "admin", "sa"])
async def test_value_change_thresholds_exact(client, world, admin_engine, label):
    eng = admin_engine
    _, actor, owner = next(x for x in role_setup(world) if x[0] == label)
    prod = await product(client, world, "Penny", 1, 0)
    bad = []
    for new_qty, pct in CASES:
        did, _o = await mkdeal(client, world, eng)
        await set_owner(eng, did, owner)
        lid = (
            await add_line(
                client,
                owner if owner.role != "super_admin" else world.admin,
                did,
                prod["id"],
                BASE_QTY,
                reason="base",
            )
        ).json()["lines"][0]["id"]
        assert await value(eng, did) == BASE_QTY
        status, needs_reason = expected_value_outcome(label, pct)
        tag = f"{label} {pct}% ({BASE_QTY}->{new_qty})"
        mark = await max_seq(eng)
        if needs_reason:
            r = await client.patch(
                f"/deals/{did}/lines/{lid}", json={"quantity": new_qty}, headers=H(actor)
            )
            if r.status_code != 422 or r.json()["code"] != "reason_required":
                bad.append(f"{tag}: without a reason got {r.status_code} {r.text[:80]}")
            assert await value(eng, did) == BASE_QTY
        mark = await max_seq(eng)
        r = await client.patch(
            f"/deals/{did}/lines/{lid}",
            json={"quantity": new_qty, "reason": "customer asked"},
            headers=H(actor),
        )
        if r.status_code != status:
            bad.append(f"{tag}: got {r.status_code}, expected {status}: {r.text[:100]}")
            continue
        if status == 202:
            assert await value(eng, did) == BASE_QTY, "value must not change while pending"
            ap = (
                await client.get(f"/approvals/{r.json()['approval_id']}", headers=H(world.admin))
            ).json()
            want_route = "team_leader" if label == "user_owner" else "admin_pool"
            if (ap["type"], ap["routed_to"]) != ("deal.value_change", want_route):
                bad.append(f"{tag}: approval {ap['type']} {ap['routed_to']}")
            if ap["request_reason"] != "customer asked":
                bad.append(f"{tag}: approval reason {ap['request_reason']}")
            a = [x for x in await audit_rows(eng, did) if x["seq"] > mark]
            if [(x["outcome"], x["action_key"]) for x in a] != [
                ("approval_requested", "deal.value.above_10")
            ]:
                bad.append(f"{tag}: audit {[x['outcome'] for x in a]}")
        else:
            if await value(eng, did) != new_qty:
                bad.append(f"{tag}: value {await value(eng, did)}")
            a = [x for x in await audit_rows(eng, did) if x["seq"] > mark and x["outcome"] == "ok"]
            want_key = (
                "deal.value.up_to_5"
                if pct <= 5
                else "deal.value.5_to_10"
                if pct <= 10
                else "deal.value.above_10"
            )
            if len(a) != 1 or a[0]["action_key"] != want_key:
                bad.append(
                    f"{tag}: audit {[(x['action_key'], x['reason']) for x in a]} wanted {want_key}"
                )
    assert not bad, "\n".join(bad)


async def test_value_change_from_zero_and_by_non_owners(client, world, admin_engine):
    eng = admin_engine
    prod = await product(client, world, "Penny", 1, 0)
    did, owner = await mkdeal(client, world, eng)
    # the first line on an empty deal is free whatever its size (nothing to compare with)
    await add_line(client, owner, did, prod["id"], 5_000_000)
    assert await value(eng, did) == 5_000_000
    # a rep who is not the owner (same team), another team's rep and TL, cannot touch lines
    lid = (await lines_of(client, owner, did))["lines"][0]["id"]
    for actor in (world.north2, world.south1, world.tl_south):
        await patch_line(client, actor, did, lid, expect=403, quantity=5_000_001, reason="x")
        await add_line(client, actor, did, prod["id"], 1, expect=403, reason="x")
        r = await client.delete(
            f"/deals/{did}/lines/{lid}", params={"reason": "x"}, headers=H(actor)
        )
        assert r.status_code == 403
        assert (
            await client.get(f"/deals/{did}/lines", headers=H(actor))
        ).status_code == 404  # reads do not leak
    # the team leader of the team can (team scope)
    await patch_line(client, world.tl_north, did, lid, quantity=5_000_100)
    # removing the only line is a 100% change: a rep needs approval, an Admin needs a reason
    r = await client.delete(
        f"/deals/{did}/lines/{lid}", params={"reason": "dropped"}, headers=H(owner)
    )
    assert r.status_code == 202
    r = await client.delete(f"/deals/{did}/lines/{lid}", headers=H(world.admin))
    assert r.status_code == 422
    r = await client.delete(
        f"/deals/{did}/lines/{lid}", params={"reason": "dropped"}, headers=H(world.admin)
    )
    assert r.status_code == 200 and (await value(eng, did)) == 0
    assert not await check_invariants(eng, world.org_id)


@pytest.mark.parametrize("bad_qty", [0, -1, -1000000, 1.5, None, "abc"])
async def test_quantity_validation(client, world, admin_engine, bad_qty):
    prod = await product(client, world, "Penny", 1, 0)
    did, owner = await mkdeal(client, world, admin_engine)
    r = await client.post(
        f"/deals/{did}/lines",
        json={"product_id": prod["id"], "quantity": bad_qty},
        headers=H(owner),
    )
    assert r.status_code == 422, (bad_qty, r.status_code, r.text[:100])


async def test_quantity_extremes(client, world, admin_engine):
    eng = admin_engine
    prod = await product(client, world, "Penny", 600, 450, unit="per_minute")
    did, owner = await mkdeal(client, world, eng)
    r = await add_line(client, owner, did, prod["id"], 1)
    assert r.json()["value_paise"] == 600
    lid = r.json()["lines"][0]["id"]
    await patch_line(client, world.admin, did, lid, quantity=1_000_000, reason="big")
    assert await value(eng, did) == 600_000_000
    # past 32-bit integers the answer must still be a clean refusal, never a 500
    for q in (2**31 - 1,):
        r = await client.patch(
            f"/deals/{did}/lines/{lid}",
            json={"quantity": q, "reason": "huge"},
            headers=H(world.admin),
        )
        assert r.status_code < 500, (q, r.status_code)


async def test_BUG_P3_14_quantity_above_int32(client, world, admin_engine):
    prod = await product(client, world, "Penny", 600, 450)
    did, owner = await mkdeal(client, world, admin_engine)
    r = await client.post(
        f"/deals/{did}/lines", json={"product_id": prod["id"], "quantity": 2**31}, headers=H(owner)
    )
    assert r.status_code == 422, r.status_code


def half_up(qty: int, price: int, disc: str) -> int:
    v = Decimal(qty) * Decimal(price) * (Decimal(100) - Decimal(disc)) / 100
    return int(v.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@pytest.mark.parametrize("disc", ["0.01", "0.5", "33.33", "99.99", "100", "10", "12.5", "7.25"])
@pytest.mark.parametrize("qty,price", [(1, 1), (3, 333), (7, 1), (1000, 600), (13, 99), (1, 5)])
async def test_line_total_rounding_is_half_up(client, world, admin_engine, disc, qty, price):
    prod = await product(client, world, f"P{price}", price, 0)
    did, _owner = await mkdeal(client, world, admin_engine)
    r = await add_line(client, world.admin, did, prod["id"], qty, discount_pct=disc, reason="qa")
    ln = r.json()["lines"][0]
    assert ln["line_total_paise"] == half_up(qty, price, disc), (qty, price, disc)
    assert (await value(admin_engine, did)) == ln["line_total_paise"]


async def test_BUG_P3_15_discount_precision(client, world, admin_engine):
    prod = await product(client, world, "Pk", 1000, 0)
    did, _o = await mkdeal(client, world, admin_engine)
    r = await add_line(
        client, world.admin, did, prod["id"], 1000, discount_pct="33.333", reason="qa"
    )
    ln = r.json()["lines"][0]
    recomputed = half_up(ln["quantity"], ln["unit_price_paise"], str(ln["discount_pct"]))
    assert ln["line_total_paise"] == recomputed, (
        ln["discount_pct"],
        ln["line_total_paise"],
        recomputed,
    )


@pytest.mark.parametrize("disc", ["-0.01", "100.01", "abc", "NaN", "Infinity", "1e2"])
async def test_discount_bounds(client, world, admin_engine, disc):
    prod = await product(client, world, "Pk", 1000, 0)
    did, _o = await mkdeal(client, world, admin_engine)
    r = await client.post(
        f"/deals/{did}/lines",
        json={"product_id": prod["id"], "quantity": 1, "discount_pct": disc, "reason": "x"},
        headers=H(world.admin),
    )
    assert r.status_code == 422 or (disc == "1e2" and r.status_code == 201), (
        disc,
        r.status_code,
        r.text[:100],
    )


# ------------------------------------------------------------------------------------------ discounts
def discount_expect(label, pct: Decimal, price_after: Decimal, floor: int):
    """('direct'|'approval_tl'|'approval_admin', needs_reason) per PRD 4.4 G."""
    over = pct > 10 or price_after < floor
    if label == "user_owner":
        return ("approval_admin" if over else "approval_tl", True)
    if label in ("tl_owner", "tl_team_deal"):
        return ("approval_admin", True) if over else ("direct", True)
    if label == "admin":
        return ("direct", True)
    return ("direct", False)


DISCOUNT_CASES = [
    # list, floor, discount, label of the situation
    (1000, 900, "5", "small discount"),
    (1000, 900, "10", "exactly 10% and exactly the floor"),
    (1000, 900, "10.01", "just above 10%"),
    (1000, 900, "12", "12%"),
    (1000, 995, "1", "small discount but under the floor"),
    (1000, 990, "1", "1% lands exactly on the floor"),
    (1000, 0, "0.01", "tiny"),
    (1000, 0, "0", "list price"),
]


@pytest.mark.parametrize("label", ["user_owner", "tl_owner", "tl_team_deal", "admin", "sa"])
async def test_discount_matrix(client, world, admin_engine, label):
    eng = admin_engine
    _, actor, owner = next(x for x in role_setup(world) if x[0] == label)
    bad = []
    for list_p, floor, disc, why in DISCOUNT_CASES:
        prod = await product(client, world, f"D{list_p}-{floor}", list_p, floor)
        did, _o = await mkdeal(client, world, eng)
        await set_owner(eng, did, owner)
        pct = Decimal(disc)
        price_after = Decimal(list_p) * (100 - pct) / 100
        if pct == 0:
            kind, needs = "direct", False
        else:
            kind, needs = discount_expect(label, pct, price_after, floor)
        tag = f"{label} {why} ({disc}%)"
        mark = await max_seq(eng)
        # a discount at the time of adding the line
        if needs:
            r = await client.post(
                f"/deals/{did}/lines",
                json={"product_id": prod["id"], "quantity": 10, "discount_pct": disc},
                headers=H(actor),
            )
            if r.status_code != 422 or r.json()["code"] != "reason_required":
                bad.append(f"{tag}: add without reason -> {r.status_code} {r.text[:80]}")
        r = await client.post(
            f"/deals/{did}/lines",
            json={
                "product_id": prod["id"],
                "quantity": 10,
                "discount_pct": disc,
                "reason": "competitor offer",
            },
            headers=H(actor),
        )
        want = 202 if kind.startswith("approval") else 201
        if r.status_code != want:
            bad.append(f"{tag}: got {r.status_code} {r.text[:100]}, wanted {want}")
            continue
        ln = (await lines_of(client, world.admin, did))["lines"]
        if kind == "direct":
            exp_total = half_up(10, list_p, disc)
            if (
                not ln
                or ln[0]["line_total_paise"] != exp_total
                or await value(eng, did) != exp_total
            ):
                bad.append(
                    f"{tag}: direct but total {ln[0]['line_total_paise'] if ln else None} != {exp_total}"
                )
            a = [x for x in await audit_rows(eng, did) if x["seq"] > mark and x["outcome"] == "ok"]
            if len(a) != 1:
                bad.append(f"{tag}: audit rows {[(x['action_key']) for x in a]}")
        else:
            # the line exists at list price with the discount waiting; the value is not reduced yet
            if (
                not ln
                or ln[0]["discount_status"] != "pending"
                or Decimal(str(ln[0]["pending_discount_pct"])) != pct
            ):
                bad.append(f"{tag}: line {ln}")
            elif (
                ln[0]["discount_pct"] not in (0, "0", "0.00", Decimal(0))
                and Decimal(str(ln[0]["discount_pct"])) != 0
            ):
                bad.append(f"{tag}: discount already applied {ln[0]['discount_pct']}")
            elif await value(eng, did) != list_p * 10:
                bad.append(f"{tag}: value {await value(eng, did)} while pending")
            ap = (
                await client.get(f"/approvals/{r.json()['approval_id']}", headers=H(world.admin))
            ).json()
            want_type = (
                "discount.above_10_or_below_floor"
                if kind == "approval_admin"
                else "discount.up_to_10"
            )
            want_route = "admin_pool" if kind == "approval_admin" else "team_leader"
            if (ap["type"], ap["routed_to"]) != (want_type, want_route):
                bad.append(
                    f"{tag}: approval {ap['type']}/{ap['routed_to']} wanted {want_type}/{want_route}"
                )
            # whoever the request is routed to approves it; the line takes the discount once
            approver = world.admin if kind == "approval_admin" else world.tl_north
            if kind == "approval_tl" and owner.team_id != world.tl_north.team_id:
                approver = world.admin
            rr = await client.post(
                f"/approvals/{ap['id']}/decide",
                json={"decision": "approve", "reason": "ok"},
                headers=H(approver),
            )
            if rr.status_code != 200:
                bad.append(f"{tag}: approve {rr.status_code} {rr.text[:100]}")
            else:
                ln = (await lines_of(client, world.admin, did))["lines"][0]
                exp_total = half_up(10, list_p, disc)
                if (ln["line_total_paise"], ln["discount_status"]) != (
                    exp_total,
                    "approved",
                ) or await value(eng, did) != exp_total:
                    bad.append(f"{tag}: after approval {ln}")
                again = await client.post(
                    f"/approvals/{ap['id']}/decide",
                    json={"decision": "approve", "reason": "ok"},
                    headers=H(approver),
                )
                if again.status_code != 409:
                    bad.append(f"{tag}: second approval -> {again.status_code}")
    assert not bad, "\n".join(bad)
    assert not await check_invariants(eng, world.org_id)


async def test_raise_lower_and_repeat_a_discount_on_an_existing_line(client, world, admin_engine):
    eng = admin_engine
    prod = await product(client, world, "Pd", 1000, 500)
    did, owner = await mkdeal(client, world, eng)
    lid = (await add_line(client, owner, did, prod["id"], 100)).json()["lines"][0]["id"]
    # raise to 5%: request (TL). Lowering or repeating is free once approved
    r = await patch_line(client, owner, did, lid, expect=202, discount_pct="5", reason="competitor")
    await client.post(
        f"/approvals/{r.json()['approval_id']}/decide",
        json={"decision": "approve", "reason": "ok"},
        headers=H(world.tl_north),
    )
    assert await value(eng, did) == 95000
    await patch_line(client, owner, did, lid, discount_pct="3")  # lowering: no approval, no reason
    assert await value(eng, did) == 97000
    await patch_line(
        client, owner, did, lid, discount_pct="3"
    )  # unchanged: nothing happens (no 202)
    await patch_line(client, owner, did, lid, discount_pct="0")
    assert await value(eng, did) == 100000
    # raising a discount that already existed needs approval for the whole new level
    await patch_line(client, world.admin, did, lid, discount_pct="4", reason="r")
    r = await patch_line(client, owner, did, lid, expect=202, discount_pct="6", reason="more")
    ap = (await client.get(f"/approvals/{r.json()['approval_id']}", headers=H(world.admin))).json()
    assert ap["payload"]["discount_pct"] == "6" or ap["payload"]["discount_pct"] == "6.00"


async def test_BUG_P3_16_two_pending_requests_on_one_line(client, world, admin_engine):
    eng = admin_engine
    prod = await product(client, world, "Pd", 1000, 500)
    did, owner = await mkdeal(client, world, eng)
    lid = (await add_line(client, owner, did, prod["id"], 100)).json()["lines"][0]["id"]
    r1 = await patch_line(client, owner, did, lid, expect=202, discount_pct="5", reason="first")
    r2 = await patch_line(client, owner, did, lid, expect=202, discount_pct="8", reason="second")
    a1, a2 = r1.json()["approval_id"], r2.json()["approval_id"]
    assert a1 != a2
    # approve the OLDER request: it must not silently win over the newer, still-pending one
    await client.post(
        f"/approvals/{a1}/decide",
        json={"decision": "approve", "reason": "ok"},
        headers=H(world.tl_north),
    )
    states = [
        (await client.get(f"/approvals/{a}", headers=H(world.admin))).json()["status"]
        for a in (a1, a2)
    ]
    line = (await lines_of(client, owner, did))["lines"][0]
    # either the older one is refused, or it is applied and the newer one is closed with it; but a
    # request must never stay pending for a line that is no longer waiting for it
    still_pending = [a for a, st in zip((a1, a2), states, strict=True) if st == "pending"]
    assert not (still_pending and line["discount_status"] != "pending"), (states, line)


async def test_line_with_pending_discount_deleted_and_requests_left_behind(
    client, world, admin_engine
):
    eng = admin_engine
    prod = await product(client, world, "Pd", 1000, 500)
    did, owner = await mkdeal(client, world, eng)
    lid = (await add_line(client, owner, did, prod["id"], 100)).json()["lines"][0]["id"]
    r = await patch_line(client, owner, did, lid, expect=202, discount_pct="5", reason="x")
    ap = r.json()["approval_id"]
    await client.delete(
        f"/deals/{did}/lines/{lid}", params={"reason": "gone"}, headers=H(world.admin)
    )
    r = await client.post(
        f"/approvals/{ap}/decide",
        json={"decision": "approve", "reason": "ok"},
        headers=H(world.tl_north),
    )
    assert r.status_code in (404, 409), r.status_code  # clean refusal, never a 500
    r = await client.post(
        f"/approvals/{ap}/decide",
        json={"decision": "reject", "reason": "line is gone"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200  # and it can be cleared by rejecting
    assert not await check_invariants(eng, world.org_id)


async def test_approval_lifecycle_requester_cannot_approve_reject_and_cancel(
    client, world, admin_engine
):
    eng = admin_engine
    prod = await product(client, world, "Pd", 1000, 500)
    did, owner = await mkdeal(client, world, eng)
    lid = (await add_line(client, owner, did, prod["id"], 100)).json()["lines"][0]["id"]
    # value change >10% (qty x3), pending: nothing changes
    r = await patch_line(client, owner, did, lid, expect=202, quantity=300, reason="expansion")
    ap = r.json()["approval_id"]
    assert await value(eng, did) == 100000
    assert (await client.get(f"/deals/{did}/lines", headers=H(owner))).json()["pending_requests"][
        0
    ]["approval_id"] == ap
    for actor, code in (
        (owner, 403),
        (world.north2, 403),
        (world.tl_south, 403),
        (world.south1, 403),
    ):
        r = await client.post(
            f"/approvals/{ap}/decide", json={"decision": "approve", "reason": "x"}, headers=H(actor)
        )
        assert r.status_code == code, (actor.name, r.status_code)
    # rejected: nothing changes and it cannot be approved afterwards
    r = await client.post(
        f"/approvals/{ap}/decide",
        json={"decision": "reject", "reason": "too big"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 200 and await value(eng, did) == 100000
    r = await client.post(
        f"/approvals/{ap}/decide",
        json={"decision": "approve", "reason": "x"},
        headers=H(world.admin),
    )
    assert r.status_code == 409
    # cancelled by the requester only
    r = await patch_line(client, owner, did, lid, expect=202, quantity=300, reason="again")
    ap2 = r.json()["approval_id"]
    r = await client.post(f"/approvals/{ap2}/cancel", headers=H(world.tl_north))
    assert r.status_code == 404
    r = await client.post(f"/approvals/{ap2}/cancel", headers=H(owner))
    assert r.status_code == 200 and await value(eng, did) == 100000
    # approving the same request from two approvers at once applies it exactly once
    r = await patch_line(client, owner, did, lid, expect=202, quantity=300, reason="third")
    ap3 = r.json()["approval_id"]
    res = await asyncio.gather(
        client.post(
            f"/approvals/{ap3}/decide",
            json={"decision": "approve", "reason": "ok"},
            headers=H(world.tl_north),
        ),
        client.post(
            f"/approvals/{ap3}/decide",
            json={"decision": "approve", "reason": "ok"},
            headers=H(world.admin),
        ),
        client.post(
            f"/approvals/{ap3}/decide",
            json={"decision": "approve", "reason": "ok"},
            headers=H(world.sa),
        ),
    )
    assert sorted(x.status_code for x in res) == [200, 409, 409]
    assert await value(eng, did) == 300000
    assert not await check_invariants(eng, world.org_id)


async def test_stale_value_request_when_the_deal_is_lost_or_line_gone(client, world, admin_engine):
    eng = admin_engine
    prod = await product(client, world, "Pd", 1000, 500)
    did, owner = await mkdeal(client, world, eng)
    lid = (await add_line(client, owner, did, prod["id"], 100)).json()["lines"][0]["id"]
    ap = (await patch_line(client, owner, did, lid, expect=202, quantity=300, reason="x")).json()[
        "approval_id"
    ]
    await client.post(f"/deals/{did}/lose", json={"lost_reason": "Price"}, headers=H(owner))
    r = await client.post(
        f"/approvals/{ap}/decide",
        json={"decision": "approve", "reason": "ok"},
        headers=H(world.tl_north),
    )
    assert r.status_code == 409  # the deal is no longer open
    assert await value(eng, did) == 100000
    # lines on a lost deal are refused, reads still work
    await add_line(client, owner, did, prod["id"], 1, expect=409)
    await patch_line(client, world.admin, did, lid, expect=409, quantity=1, reason="x")
    # the request for a deleted line
    did2, owner2 = await mkdeal(client, world, eng)
    lid2 = (await add_line(client, owner2, did2, prod["id"], 100)).json()["lines"][0]["id"]
    ap2 = (
        await patch_line(client, owner2, did2, lid2, expect=202, quantity=300, reason="x")
    ).json()["approval_id"]
    await client.delete(
        f"/deals/{did2}/lines/{lid2}", params={"reason": "gone"}, headers=H(world.admin)
    )
    r = await client.post(
        f"/approvals/{ap2}/decide",
        json={"decision": "approve", "reason": "ok"},
        headers=H(world.tl_north),
    )
    assert r.status_code in (404, 409)


async def test_catalog_rules_prices_floor_roles_and_frozen_lines(client, world, admin_engine):
    eng = admin_engine
    base = {
        "name": "Voice",
        "unit": "per_minute",
        "list_price_paise": 600,
        "floor_price_paise": 450,
    }
    put = lambda items, actor=None, reason="qa": client.put(  # noqa: E731
        "/products", json={"products": items, "reason": reason}, headers=H(actor or world.admin)
    )
    assert (await put([base], world.tl_north)).status_code == 403
    assert (await put([base], world.north1)).status_code == 403
    assert (await put([base], reason=None)).status_code == 422
    assert (await put([{**base, "floor_price_paise": 601}])).status_code == 422  # floor above list
    assert (await put([{**base, "list_price_paise": -1}])).status_code == 422
    assert (await put([{**base, "unit": "per_hour"}])).status_code == 422
    assert (await put([{**base, "name": ""}])).status_code == 422
    assert (await put([{**base, "name": "x" * 201}])).status_code == 422
    assert (await put([{**base, "foo": 1}])).status_code == 422
    assert (
        await put([{**base, "floor_price_paise": 600}])
    ).status_code == 200  # floor == list is fine
    assert (
        await one(
            eng,
            "SELECT count(*) FROM audit_log WHERE org_id=:o AND action_key='catalog.template.manage' AND outcome='ok'",
            o=world.org_id,
        )
        == 1
    )
    # a rejected list stores nothing from it (first valid item must not survive the bad second one)
    r = await put([{**base, "name": "Valid"}, {**base, "name": "Bad", "floor_price_paise": 9999}])
    assert r.status_code == 422
    names = [
        p["name"]
        for p in (
            await client.get("/products", params={"include_inactive": True}, headers=H(world.admin))
        ).json()
    ]
    assert "Valid" not in names
    # a price change and a deactivation never touch lines already on deals
    prod = (await put([base])).json()[0]
    did, owner = await mkdeal(client, world, eng)
    await add_line(client, owner, did, prod["id"], 100)
    assert await value(eng, did) == 60000
    await put([{**base, "id": prod["id"], "list_price_paise": 900, "floor_price_paise": 100}])
    assert await value(eng, did) == 60000
    ln = (await lines_of(client, owner, did))["lines"][0]
    assert ln["unit_price_paise"] == 600
    await put([{**base, "id": prod["id"], "active": False}])
    await add_line(client, owner, did, prod["id"], 1, expect=422)  # cannot add an inactive product
    await patch_line(client, owner, did, ln["id"], quantity=102)  # existing line can still change
    assert await value(eng, did) == 61200
    listed = (await client.get("/products", headers=H(owner))).json()
    assert prod["id"] not in [p["id"] for p in listed]
    # another org's product id is refused
    from tests.deep.phase3.p3 import second_org

    other = await second_org(eng)
    alien = await one(
        eng,
        "INSERT INTO products (org_id,name,unit,list_price_paise,floor_price_paise) VALUES (:o,'Alien','fixed',1,0) RETURNING id",
        o=other,
    )
    await add_line(client, owner, did, str(alien), 1, expect=422)


async def test_line_idor_and_extra_fields(client, world, admin_engine):
    eng = admin_engine
    prod = await product(client, world, "Pd", 1000, 500)
    d1, o1 = await mkdeal(client, world, eng)
    d2, o2 = await mkdeal(client, world, eng, owner=world.south1)
    l1 = (await add_line(client, o1, d1, prod["id"], 10)).json()["lines"][0]["id"]
    # a line of deal 1 addressed through deal 2 (owner of deal 2 acts)
    await patch_line(client, o2, d2, l1, expect=404, quantity=11)
    r = await client.delete(f"/deals/{d2}/lines/{l1}", headers=H(o2))
    assert r.status_code == 404
    await add_line(client, o1, d1, prod["id"], 1, expect=422, status="x")  # extra field
    r = await client.post(
        f"/deals/{d1}/lines",
        json={"product_id": prod["id"], "quantity": 1, "unit_price_paise": 1},
        headers=H(o1),
    )
    assert r.status_code == 422  # the price cannot be set by the caller
    r = await client.patch(f"/deals/{d1}/lines/{l1}", json={"line_total_paise": 1}, headers=H(o1))
    assert r.status_code == 422
    assert (await value(eng, d1)) == 10000
    await run(eng, "UPDATE deal_products SET deleted_at=now() WHERE id=:i", i=l1)
    await patch_line(client, o1, d1, l1, expect=404, quantity=2)
