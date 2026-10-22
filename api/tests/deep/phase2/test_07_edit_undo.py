# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 2 deep test, section 6: editing a lead and the 10-second Undo."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from tests.deep.phase2.support import (
    audit_rows,
    configure,
    create,
    due,
    headers,
    q,
    q1,
)


async def lead_and_owner(qc, world):
    await configure(qc, world)
    d = await create(qc, world.north1, company="Edit Probe Foods")
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    return d, owner


def patch(qc, actor, deal_id, **body):
    return qc.patch(f"/deals/{deal_id}", json=body, headers=headers(actor))


def undo(qc, actor, deal_id, audit_id):
    return qc.post(f"/deals/{deal_id}/undo", json={"audit_id": audit_id}, headers=headers(actor))


# ------------------------------------------------------------------------------ editable fields
async def test_editable_fields_round_trip_and_write_one_audit_row_each(qc, admin_engine, world):
    d, owner = await lead_and_owner(qc, world)
    edits = [
        {"lead_source": "Event"},
        {"first_contact_date": "2026-01-02"},
        {"next_step_text": "Send the brochure"},
        {"next_step_due_date": due(5)},
        {"next_step_text": "Both at once", "next_step_due_date": due(6)},
    ]
    for body in edits:
        r = await patch(qc, owner, d["id"], **body)
        assert r.status_code == 200, (body, r.text)
        for k, v in body.items():
            got = r.json().get(k) or r.json()["next_step"].get(k.replace("next_step_", ""))
            assert str(got) == str(v), (k, got)
        assert r.json()["last_audit_id"]
    rows = [tuple(x)[:2] for x in await audit_rows(admin_engine, d["id"])]
    assert rows.count(("deal.edit", "ok")) == len(edits)
    h = (await qc.get(f"/history/deal/{d['id']}", headers=headers(owner))).json()
    first = next(x for x in h if x["action"] == "deal.edit" and x["new"].get("lead_source"))
    assert (
        first["old"] == {"lead_source": "Website"}
        and first["new"] == {"lead_source": "Event"}
        and first["origin"] == "screen"
    )


async def test_a_no_op_edit_writes_nothing(qc, admin_engine, world):
    d, owner = await lead_and_owner(qc, world)
    before = await q1(
        admin_engine, "SELECT count(*) FROM audit_log WHERE org_id=:o", o=world.org_id
    )
    r = await patch(qc, owner, d["id"], lead_source="Website")
    assert (
        r.status_code == 200
        and r.json()["last_audit_id"] is None
        and r.json()["version"] == d["version"]
    )
    assert before == await q1(
        admin_engine, "SELECT count(*) FROM audit_log WHERE org_id=:o", o=world.org_id
    )
    assert (await patch(qc, owner, d["id"])).status_code == 422  # nothing to change


@pytest.mark.parametrize(
    "field",
    [
        "created_by",
        "generated_by",
        "created_at",
        "id",
        "owner_id",
        "team_id",
        "status",
        "account_id",
        "company",
        "org_id",
        "version",
        "assigned_at",
        "assignment_method",
        "stage_key",
        "deleted_at",
    ],
)
async def test_locked_and_unknown_fields_are_rejected_for_every_role(
    qc, admin_engine, world, field
):
    d, owner = await lead_and_owner(qc, world)
    before = await q(
        admin_engine,
        "SELECT created_by, generated_by, created_at, owner_id, status, version FROM deals WHERE id=:i",
        i=uuid.UUID(d["id"]),
    )
    for actor in (owner, world.tl_north, world.admin, world.sa):
        r = await patch(qc, actor, d["id"], **{field: str(uuid.uuid4())})
        assert r.status_code == 422, (actor.name, field, r.text)
    assert before == await q(
        admin_engine,
        "SELECT created_by, generated_by, created_at, owner_id, status, version FROM deals WHERE id=:i",
        i=uuid.UUID(d["id"]),
    )


@pytest.mark.parametrize(
    "body",
    [
        {"lead_source": None},
        {"next_step_text": None},
        {"next_step_due_date": None},
        {"next_step_text": ""},
        {"lead_source": "Carrier pigeon"},
        {"next_step_text": "x" * 501},
        {"next_step_due_date": "yesterday"},
    ],
)
async def test_required_fields_are_never_emptied_or_invalid(qc, admin_engine, world, body):
    d, owner = await lead_and_owner(qc, world)
    r = await patch(qc, owner, d["id"], **body)
    assert r.status_code == 422, r.text
    got = (await qc.get(f"/deals/{d['id']}", headers=headers(owner))).json()
    assert got["lead_source"] == "Website" and got["next_step"]["text"] == "Call to qualify"


async def test_whitespace_only_next_step_is_refused_on_edit(qc, world):
    d, owner = await lead_and_owner(qc, world)
    r = await patch(qc, owner, d["id"], next_step_text="     ")
    assert r.status_code == 422


async def test_next_step_due_date_boundaries_on_edit(qc, world):
    d, owner = await lead_and_owner(qc, world)
    assert (await patch(qc, owner, d["id"], next_step_due_date=due(-1))).status_code == 422
    assert (await patch(qc, owner, d["id"], next_step_due_date=due(0))).status_code == 200
    assert (await patch(qc, owner, d["id"], next_step_text="y" * 500)).status_code == 200


async def test_first_contact_date_can_be_cleared(qc, world):
    d, owner = await lead_and_owner(qc, world)
    assert (await patch(qc, owner, d["id"], first_contact_date="2026-01-02")).json()[
        "first_contact_date"
    ] == "2026-01-02"
    r = await patch(qc, owner, d["id"], first_contact_date=None)
    assert r.status_code == 200 and r.json()["first_contact_date"] is None


async def test_edit_roles_and_scope(qc, admin_engine, world):
    d, owner = await lead_and_owner(qc, world)
    other_rep = world.north2 if owner.id == world.north1.id else world.north1
    assert (await patch(qc, other_rep, d["id"], lead_source="Event")).status_code in (
        403,
        404,
    )  # same team, not owner
    assert (await patch(qc, world.south1, d["id"], lead_source="Event")).status_code in (403, 404)
    assert (await patch(qc, world.tl_south, d["id"], lead_source="Event")).status_code in (403, 404)
    assert (await patch(qc, world.tl_north, d["id"], lead_source="Event")).status_code == 200
    assert (
        await patch(qc, world.admin, d["id"], lead_source="Referral")
    ).status_code == 422  # Admin needs a reason
    assert (
        await patch(qc, world.admin, d["id"], lead_source="Referral", reason="fix")
    ).status_code == 200
    assert (await patch(qc, world.sa, d["id"], lead_source="LinkedIn")).status_code == 200
    assert (await qc.patch(f"/deals/{d['id']}", json={"lead_source": "Event"})).status_code == 401
    assert (await patch(qc, owner, str(uuid.uuid4()), lead_source="Event")).status_code == 404
    assert (await patch(qc, owner, "not-a-uuid", lead_source="Event")).status_code == 422


async def test_other_organisations_cannot_edit(qc, admin_engine, world):
    from tests.deep.phase2.support import make_world

    d, owner = await lead_and_owner(qc, world)
    other = await make_world(admin_engine)
    for actor in (other.admin, other.sa, other.tl_north):
        assert (await patch(qc, actor, d["id"], lead_source="Event", reason="x")).status_code == 404
    r = await patch(qc, owner, d["id"], lead_source="Event")
    assert (await undo(qc, other.admin, d["id"], r.json()["last_audit_id"])).status_code == 404


async def test_a_lost_or_deleted_lead_cannot_be_edited(qc, world):
    d, owner = await lead_and_owner(qc, world)
    await qc.post(f"/deals/{d['id']}/lose", json={"lost_reason": "Price"}, headers=headers(owner))
    assert (await patch(qc, owner, d["id"], lead_source="Event")).status_code == 409
    d2 = await create(qc, world.north1)
    await qc.post(
        f"/deals/{d2['id']}/delete", json={"reason": "junk"}, headers=headers(world.admin)
    )
    assert (
        await patch(qc, world.admin, d2["id"], lead_source="Event", reason="x")
    ).status_code == 404


# ------------------------------------------------------------------------------ version conflicts
async def test_version_conflict(qc, world):
    d, owner = await lead_and_owner(qc, world)
    v = d["version"]
    r = await patch(qc, owner, d["id"], lead_source="Event", expected_version=v + 5)
    assert r.status_code == 409 and r.json()["details"]["current_version"] == v
    r = await patch(qc, owner, d["id"], lead_source="Event", expected_version=v)
    assert r.status_code == 200 and r.json()["version"] == v + 1
    r = await patch(qc, owner, d["id"], lead_source="Referral", expected_version=v)  # stale now
    assert r.status_code == 409
    assert (
        await patch(qc, owner, d["id"], lead_source="Referral", expected_version=0)
    ).status_code == 409
    assert (
        await patch(qc, owner, d["id"], lead_source="Referral", expected_version=-1)
    ).status_code == 409
    assert (
        await patch(qc, owner, d["id"], lead_source="Referral", expected_version="x")
    ).status_code == 422


async def test_two_edits_with_the_same_expected_version_one_wins(qc, admin_engine, world):
    d, owner = await lead_and_owner(qc, world)
    v = d["version"]
    rs = await asyncio.gather(
        *(
            patch(qc, owner, d["id"], lead_source=s, expected_version=v)
            for s in ("Event", "Referral", "LinkedIn", "Cold outbound")
        )
    )
    assert sorted(r.status_code for r in rs) == [200, 409, 409, 409]
    assert (
        await q1(
            admin_engine,
            "SELECT count(*) FROM audit_log WHERE entity_id=:e AND action_key='deal.edit' AND outcome='ok'",
            e=d["id"],
        )
        == 1
    )


async def test_a_bad_version_check_with_a_missing_reason_does_not_leak_a_change(
    qc, admin_engine, world
):
    d, owner = await lead_and_owner(qc, world)
    r = await patch(qc, world.admin, d["id"], lead_source="Event", expected_version=999)
    assert r.status_code in (409, 422)
    assert (await qc.get(f"/deals/{d['id']}", headers=headers(owner))).json()[
        "lead_source"
    ] == "Website"


# ------------------------------------------------------------------------------ source after Lead
async def test_changing_the_source_after_the_lead_stage_needs_its_own_permission(
    qc, admin_engine, world
):
    d, owner = await lead_and_owner(qc, world)
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE deals SET stage_key='prospecting' WHERE id=:i"), {"i": d["id"]}
        )
    r = await patch(qc, owner, d["id"], lead_source="Event")
    assert r.status_code == 403  # a rep cannot
    assert (
        await patch(qc, owner, d["id"], first_contact_date="2026-01-02")
    ).status_code == 200  # other fields still can
    assert (
        await patch(qc, world.tl_north, d["id"], lead_source="Event")
    ).status_code == 422  # TL: reason needed
    ok = await patch(qc, world.tl_north, d["id"], lead_source="Event", reason="was mis-keyed")
    assert ok.status_code == 200
    row = await q(
        admin_engine,
        "SELECT action_key, reason FROM audit_log WHERE entity_id=:e AND outcome='ok' ORDER BY seq DESC LIMIT 1",
        e=d["id"],
    )
    assert tuple(row[0]) == ("deal.source.change_after_lead", "was mis-keyed")
    assert (await patch(qc, world.admin, d["id"], lead_source="Referral")).status_code == 422
    assert (
        await patch(qc, world.admin, d["id"], lead_source="Referral", reason="fix")
    ).status_code == 200
    assert (await patch(qc, world.sa, d["id"], lead_source="LinkedIn")).status_code == 200
    assert (
        await patch(qc, world.tl_south, d["id"], lead_source="Event", reason="x")
    ).status_code in (403, 404)
    denied = await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE entity_id=:e AND outcome='denied' AND action_key='deal.source.change_after_lead'",
        e=d["id"],
    )
    assert denied >= 1


async def test_a_source_that_is_unchanged_after_the_lead_stage_needs_no_extra_permission(
    qc, admin_engine, world
):
    d, owner = await lead_and_owner(qc, world)
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE deals SET stage_key='prospecting' WHERE id=:i"), {"i": d["id"]}
        )
    assert (
        await patch(
            qc, owner, d["id"], lead_source="Website", next_step_text="same source, new step"
        )
    ).status_code == 200


# ------------------------------------------------------------------------------ undo
async def edit_once(qc, owner, deal_id, **body):
    r = await patch(qc, owner, deal_id, **(body or {"lead_source": "Event"}))
    assert r.status_code == 200, r.text
    return r.json()["last_audit_id"]


async def test_undo_inside_the_window_writes_its_own_audit_row_and_keeps_the_original(
    qc, admin_engine, world
):
    d, owner = await lead_and_owner(qc, world)
    aid = await edit_once(qc, owner, d["id"])
    original = await q(
        admin_engine,
        "SELECT id, old_value, new_value, reason, row_hash FROM audit_log WHERE id=:i",
        i=uuid.UUID(aid),
    )
    r = await undo(qc, owner, d["id"], aid)
    assert r.status_code == 200 and r.json()["lead_source"] == "Website"
    assert original == await q(
        admin_engine,
        "SELECT id, old_value, new_value, reason, row_hash FROM audit_log WHERE id=:i",
        i=uuid.UUID(aid),
    )
    rows = await q(
        admin_engine,
        "SELECT action_key, old_value, new_value, note, actor_id FROM audit_log WHERE entity_id=:e AND action_key IN ('deal.edit','deal.undo') ORDER BY seq",
        e=d["id"],
    )
    assert [r_[0] for r_ in rows] == ["deal.edit", "deal.undo"]
    assert rows[1][1] == {"lead_source": "Event"} and rows[1][2] == {"lead_source": "Website"}
    assert rows[1][3] == f"undoes {aid}" and rows[1][4] == owner.id
    from app.core.db import get_sessionmaker
    from app.platform.audit.service import verify_chain

    async with get_sessionmaker()() as s:
        res = await verify_chain(s, world.org_id)
    assert res.ok


async def test_undo_twice_by_others_and_with_wrong_ids(qc, admin_engine, world):
    d, owner = await lead_and_owner(qc, world)
    aid = await edit_once(qc, owner, d["id"])
    stranger = world.north2 if owner.id == world.north1.id else world.north1
    assert (await undo(qc, stranger, d["id"], aid)).status_code in (403, 404)
    assert (await undo(qc, world.south1, d["id"], aid)).status_code in (403, 404)
    assert (
        await undo(qc, world.tl_north, d["id"], aid)
    ).status_code == 403  # a leader may edit but not undo another's edit
    assert (await undo(qc, world.admin, d["id"], aid)).status_code == 403
    assert (await undo(qc, owner, d["id"], str(uuid.uuid4()))).status_code == 404
    other = await create(qc, world.north1)
    assert (await undo(qc, owner, other["id"], aid)).status_code in (
        404,
        403,
    )  # an audit row of another deal
    assert (
        await qc.post(f"/deals/{d['id']}/undo", json={"audit_id": "x"}, headers=headers(owner))
    ).status_code == 422
    assert (
        await qc.post(f"/deals/{d['id']}/undo", json={}, headers=headers(owner))
    ).status_code == 422
    assert (await undo(qc, owner, d["id"], aid)).status_code == 200
    assert (await undo(qc, owner, d["id"], aid)).status_code == 409
    undo_rows = await q1(
        admin_engine,
        "SELECT count(*) FROM audit_log WHERE entity_id=:e AND action_key='deal.undo' AND outcome='ok'",
        e=d["id"],
    )
    assert undo_rows == 1


async def test_only_edit_rows_can_be_undone(qc, admin_engine, world):
    d, owner = await lead_and_owner(qc, world)
    create_row = await q1(
        admin_engine,
        "SELECT id FROM audit_log WHERE entity_id=:e AND action_key='lead.create'",
        e=d["id"],
    )
    assert (await undo(qc, owner, d["id"], str(create_row))).status_code == 404
    assign_row = await q1(
        admin_engine,
        "SELECT id FROM audit_log WHERE entity_id=:e AND action_key='lead.assigned'",
        e=d["id"],
    )
    assert (await undo(qc, owner, d["id"], str(assign_row))).status_code == 404
    assert (await qc.get(f"/deals/{d['id']}", headers=headers(owner))).json()["owner"]["id"] == d[
        "owner"
    ]["id"]


@pytest.mark.parametrize(
    ("age", "ok"),
    [
        (0.0, True),
        (9.0, True),
        (9.9, True),
        (10.0, True),
        (10.5, False),
        (30.0, False),
        (3600.0, False),
    ],
)
async def test_undo_window_with_a_controlled_clock(qc, world, monkeypatch, age, ok):
    from app.modules.deals import service

    d, owner = await lead_and_owner(qc, world)
    aid = await edit_once(qc, owner, d["id"])
    row_time = None
    from app.core.db import get_sessionmaker
    from app.platform.audit.models import AuditLog

    async with get_sessionmaker()() as s:
        row_time = (await s.get(AuditLog, uuid.UUID(aid))).occurred_at
    monkeypatch.setattr(service, "utc_now", lambda: row_time + timedelta(seconds=age))
    r = await undo(qc, owner, d["id"], aid)
    assert r.status_code == (200 if ok else 409), (age, r.text)
    if not ok:
        assert "10-second" in r.json()["message"]
        assert (await qc.get(f"/deals/{d['id']}", headers=headers(owner))).json()[
            "lead_source"
        ] == "Event"


async def test_undo_window_by_editing_the_audit_timestamp(qc, admin_engine, world):
    """The clock is moved by changing audit_log.occurred_at (the immutability trigger is switched
    off for the duration, in a try/finally; the chain check then detects the tampering)."""
    from app.core.db import get_sessionmaker
    from app.platform.audit.service import verify_chain

    d, owner = await lead_and_owner(qc, world)
    aid_fast = await edit_once(qc, owner, d["id"], lead_source="Event")
    aid_slow = await edit_once(qc, owner, d["id"], lead_source="Referral")
    async with admin_engine.begin() as c:
        await c.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete"))
        try:
            await c.execute(
                text(
                    "UPDATE audit_log SET occurred_at = now() - interval '11 seconds' WHERE id=:i"
                ),
                {"i": aid_slow},
            )
        finally:
            await c.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete"))
    assert (await undo(qc, owner, d["id"], aid_slow)).status_code == 409
    assert (
        await undo(qc, owner, d["id"], aid_fast)
    ).status_code == 409  # the field changed again since
    async with get_sessionmaker()() as s:
        res = await verify_chain(s, world.org_id)
    assert res.ok is False and res.problem == "row content does not match its hash"


async def test_undo_after_a_later_edit_of_the_same_field_is_refused_but_other_fields_are_fine(
    qc, world
):
    d, owner = await lead_and_owner(qc, world)
    first = await edit_once(qc, owner, d["id"], lead_source="Event")
    second = await edit_once(qc, owner, d["id"], lead_source="Referral")
    assert (await undo(qc, owner, d["id"], first)).status_code == 409
    other = await edit_once(qc, owner, d["id"], next_step_text="A different step")
    assert (await undo(qc, owner, d["id"], other)).status_code == 200
    assert (await undo(qc, owner, d["id"], second)).status_code == 200
    got = (await qc.get(f"/deals/{d['id']}", headers=headers(owner))).json()
    assert got["lead_source"] == "Event" and got["next_step"]["text"] == "Call to qualify"


async def test_undo_of_a_multi_field_edit_restores_every_field(qc, world):
    d, owner = await lead_and_owner(qc, world)
    aid = await edit_once(
        qc,
        owner,
        d["id"],
        lead_source="Event",
        next_step_text="Brand new step",
        first_contact_date="2026-02-02",
    )
    r = await undo(qc, owner, d["id"], aid)
    got = r.json()
    assert (got["lead_source"], got["next_step"]["text"], got["first_contact_date"]) == (
        "Website",
        "Call to qualify",
        None,
    )


async def test_undo_after_the_deal_changed_owner_or_was_lost(qc, world):
    d, owner = await lead_and_owner(qc, world)
    aid = await edit_once(qc, owner, d["id"])
    other = world.north2 if owner.id == world.north1.id else world.north1
    r = await qc.post(
        f"/deals/{d['id']}/reassign",
        json={"user_id": str(other.id), "reason": "balance"},
        headers=headers(world.tl_north),
    )
    assert r.status_code == 200
    assert (await undo(qc, owner, d["id"], aid)).status_code in (
        403,
        404,
    )  # the old owner lost the deal
    assert (await undo(qc, other, d["id"], aid)).status_code == 403  # not their edit
    d2, owner2 = await lead_and_owner_second(qc, world)
    aid2 = await edit_once(qc, owner2, d2["id"])
    await qc.post(f"/deals/{d2['id']}/lose", json={"lost_reason": "Price"}, headers=headers(owner2))
    r = await undo(qc, owner2, d2["id"], aid2)
    assert r.status_code in (200, 409)  # documented: undo of a lost deal's edit
    assert (
        r.status_code == 409
        or (await qc.get(f"/deals/{d2['id']}", headers=headers(owner2))).json()["lead_source"]
        == "Website"
    )


async def lead_and_owner_second(qc, world):
    d = await create(qc, world.north1, company="Second Probe Textiles")
    owner = next(a for a in world.all_actors if str(a.id) == d["owner"]["id"])
    return d, owner


async def test_undo_cannot_change_a_lost_deal(qc, world):
    d, owner = await lead_and_owner(qc, world)
    aid = await edit_once(qc, owner, d["id"])
    assert (
        await qc.post(
            f"/deals/{d['id']}/lose", json={"lost_reason": "Price"}, headers=headers(owner)
        )
    ).status_code == 200
    r = await undo(qc, owner, d["id"], aid)
    assert r.status_code == 409


async def test_undo_by_a_different_reasoned_actor_keeps_reason_rules(qc, admin_engine, world):
    d, owner = await lead_and_owner(qc, world)
    r = await patch(qc, world.admin, d["id"], lead_source="Event", reason="data fix")
    aid = r.json()["last_audit_id"]
    assert (await undo(qc, world.admin, d["id"], aid)).status_code in (200, 422)
    # the undo itself carries no reason of its own
    row = await q(
        admin_engine,
        "SELECT reason FROM audit_log WHERE entity_id=:e AND action_key='deal.undo'",
        e=d["id"],
    )
    assert all(x[0] is None for x in row)


async def test_edit_and_undo_do_not_touch_ownership_pool_or_versions_unexpectedly(
    qc, admin_engine, world
):
    d, owner = await lead_and_owner(qc, world)
    aid = await edit_once(qc, owner, d["id"])
    await undo(qc, owner, d["id"], aid)
    got = (await qc.get(f"/deals/{d['id']}", headers=headers(owner))).json()
    assert got["owner"]["id"] == d["owner"]["id"] and got["status"] == "open"
    assert got["version"] == d["version"] + 2
    assert (datetime.now(UTC) - datetime.fromisoformat(got["created_at"])).total_seconds() < 600
    assert got["created_at"] == d["created_at"] and got["generated_by"] == "Priya"
