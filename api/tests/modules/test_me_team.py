# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""/me shows the caller's own team name and its Team Leaders (never another team's)."""

from tests.helpers import headers


async def test_rep_sees_own_team_and_its_leader(client, world):
    r = await client.get("/me", headers=headers(world.north1))
    assert r.status_code == 200
    team = r.json()["team"]
    assert team["id"] == str(world.north_team)
    assert team["leaders"] == [world.tl_north.name]
    assert world.tl_south.name not in team["leaders"]


async def test_team_leader_sees_own_team(client, world):
    team = (await client.get("/me", headers=headers(world.tl_south))).json()["team"]
    assert team["id"] == str(world.south_team)
    assert team["leaders"] == [world.tl_south.name]


async def test_admin_without_a_team_has_no_team(client, world):
    for actor in (world.admin, world.sa):
        assert (await client.get("/me", headers=headers(actor))).json()["team"] is None
