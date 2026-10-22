# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 audit trail (one row per change, with its reason) and a permission sweep: every new
endpoint is refused to the roles that must not use it."""

import pytest
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.platform.audit.models import AuditLog
from tests.helpers import headers
from tests.modules.journey import due, log, new_deal, setup_catalog, tick
from tests.modules.test_leads_api import audit_keys


async def rows(entity_id, key):
    async with get_sessionmaker()() as s:
        got = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == str(entity_id),
                    AuditLog.action_key == key,
                    AuditLog.outcome == "ok",
                )
            )
        ).scalars()
        return list(got)


async def test_every_change_writes_exactly_one_audit_row_with_its_reason(client, world):
    deal, owner = await new_deal(client, world)
    did = deal["id"]
    prod = await setup_catalog(client, world)

    async def one(key, action, reason_expected=None):
        before = len(await rows(did, key))
        r = await action()
        assert r.status_code < 300, r.text
        got = await rows(did, key)
        assert len(got) == before + 1, (key, len(got) - before)
        if reason_expected:
            assert got[-1].reason == reason_expected, (key, got[-1].reason)

    H = headers
    post = client.post
    await one(
        "activity.log",
        lambda: post(
            f"/deals/{did}/activities",
            json={
                "type": "call",
                "two_way": True,
                "discussed": "x",
                "next_step_text": "n",
                "next_step_due_date": due(1),
            },
            headers=H(owner),
        ),
    )
    await one(
        "criterion.tick",
        lambda: post(f"/deals/{did}/criteria/basic_info/tick", json={}, headers=H(owner)),
    )
    await one(
        "criterion.verify",
        lambda: post(
            f"/deals/{did}/criteria/basic_info/verify",
            json={"decision": "verify"},
            headers=H(world.tl_north),
        ),
    )
    await one(
        "criterion.untick",
        lambda: post(
            f"/deals/{did}/criteria/basic_info/untick", json={"reason": "typo"}, headers=H(owner)
        ),
        "typo",
    )
    await tick(client, owner, did, "basic_info")
    await one(
        "criterion.reject",
        lambda: post(
            f"/deals/{did}/criteria/basic_info/verify",
            json={"decision": "reject", "reason": "no proof"},
            headers=H(world.tl_north),
        ),
        "no proof",
    )
    await one(
        "deal.override_or_skip",
        lambda: post(
            f"/deals/{did}/move",
            json={"to_stage": "qualification", "override": True, "reason": "away"},
            headers=H(world.tl_north),
        ),
        "away",
    )
    await one(
        "deal.move_backward",
        lambda: post(
            f"/deals/{did}/move", json={"to_stage": "lead", "reason": "wrong"}, headers=H(owner)
        ),
        "wrong",
    )
    await one(
        "deal.edit", lambda: client.patch(f"/deals/{did}", json={"use_case": "u"}, headers=H(owner))
    )
    await one(
        "deal.value.up_to_5",
        lambda: post(
            f"/deals/{did}/lines", json={"product_id": prod["id"], "quantity": 10}, headers=H(owner)
        ),
    )
    await one(
        "deal.lose",
        lambda: post(f"/deals/{did}/lose", json={"lost_reason": "Timing"}, headers=H(owner)),
        "Timing",
    )
    await one(
        "deal.reopen",
        lambda: post(f"/deals/{did}/reopen", json={"reason": "back"}, headers=H(world.tl_north)),
        "back",
    )
    await one(
        "deal.delete",
        lambda: post(f"/deals/{did}/delete", json={"reason": "test"}, headers=H(world.tl_north)),
        "test",
    )
    await one(
        "deal.restore",
        lambda: post(f"/deals/{did}/restore", json={"reason": "oops"}, headers=H(world.tl_north)),
        "oops",
    )
    # a refused attempt is logged as denied and changes nothing
    r = await post(f"/deals/{did}/criteria/basic_info/tick", json={}, headers=H(world.tl_north))
    assert r.status_code == 403
    assert ("criterion.tick", "denied") in await audit_keys(did)
    _ = log


# role -> expected outcome for requests the role must not make
SWEEP = [
    # (method, path, body, roles refused)
    ("GET", "/pipelines/new_business/versions", None, ["user", "tl"]),
    (
        "POST",
        "/pipelines/new_business/versions",
        {"from_version_no": 1, "reason": "x"},
        ["user", "tl"],
    ),
    ("PUT", "/settings/lost-reasons", {"items": [], "reason": "x"}, ["user", "tl"]),
    ("PUT", "/settings/fit-checklist", {"items": [], "reason": "x"}, ["user", "tl"]),
    ("PUT", "/settings/custom-fields", {"fields": [], "reason": "x"}, ["user", "tl"]),
    ("PUT", "/products", {"products": [], "reason": "x"}, ["user", "tl"]),
    ("GET", "/exports/log", None, ["user"]),
    ("GET", "/monitoring", None, ["user"]),
    ("GET", "/teams-compare", None, ["user", "tl"]),
    ("GET", "/deleted/deals", None, ["user"]),
    ("GET", "/pool/distribution", None, ["user"]),
]


@pytest.mark.parametrize("method,path,body,refused", SWEEP)
async def test_permission_sweep(client, world, method, path, body, refused):
    who = {"user": world.north1, "tl": world.tl_north}
    for role in refused:
        r = await client.request(method, path, json=body, headers=headers(who[role]))
        assert r.status_code == 403, (role, path, r.status_code, r.text)
    # and the roles that may, can
    allowed = world.admin
    r = await client.request(method, path, json=body, headers=headers(allowed))
    assert r.status_code < 300, (path, r.status_code, r.text)


async def test_unauthenticated_requests_are_refused_everywhere(client, world):
    for method, path, body, _ in SWEEP:
        r = await client.request(method, path, json=body)
        assert r.status_code == 401, (path, r.status_code)
    for path in ("/board", "/today", "/stages/lead/deals", "/profiles/x", "/exports/mine"):
        assert (await client.get(path)).status_code == 401
