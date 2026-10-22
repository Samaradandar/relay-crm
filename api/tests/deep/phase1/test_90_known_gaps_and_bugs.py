# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test: bugs and gaps that do not belong to one section. Each asserts the CORRECT
behaviour (these were strict xfails until the bugs were fixed)."""

from tests.deep.phase1.kit import audit_rows, h


async def test_request_only_role_cannot_rename_directly(client, world):
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"name": "Renamed by TL", "reason": "typo"},
        headers=h(world.tl_north),
    )
    assert r.status_code in (202, 403)
    from tests.helpers import get_user_row

    assert (await get_user_row(world.north2.id)).name == "Rahul"


async def test_request_only_role_cannot_rename_themselves_directly(client, world):
    r = await client.patch(
        f"/users/{world.tl_north.id}",
        json={"name": "Boss", "reason": "mine"},
        headers=h(world.tl_north),
    )
    assert r.status_code in (202, 403)


async def test_team_history_is_viewable_by_an_admin(client, world):
    tid = (
        await client.post(
            "/teams", json={"name": "Historic", "reason": "r"}, headers=h(world.admin)
        )
    ).json()["id"]
    await client.patch(
        f"/teams/{tid}", json={"name": "Historic 2", "reason": "rename"}, headers=h(world.admin)
    )
    assert [
        x["action_key"] for x in await audit_rows(world.org_id, entity_type="team", entity_id=tid)
    ] == ["team.manage", "team.manage"]
    r = await client.get(f"/history/team/{tid}", headers=h(world.admin))
    assert r.status_code == 200 and len(r.json()) == 2


async def test_failed_job_retry_history_is_viewable(client, world, admin_engine):
    from sqlalchemy import text

    async with admin_engine.begin() as c:
        eid = (
            await c.execute(
                text(
                    "INSERT INTO outbox_events (org_id,type,payload,status,attempts) VALUES (:o,'x','{}','failed',10) RETURNING id"
                ),
                {"o": world.org_id},
            )
        ).scalar_one()
    await client.post(
        f"/admin/failed-jobs/{eid}/retry", json={"reason": "r"}, headers=h(world.admin)
    )
    r = await client.get(f"/history/outbox_event/{eid}", headers=h(world.admin))
    assert r.status_code == 200


async def test_team_leader_can_request_a_deactivation(client, world):
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"is_active": False, "reason": "left the company"},
        headers=h(world.tl_north),
    )
    assert r.status_code == 202
