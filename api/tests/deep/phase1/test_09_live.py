# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 9: WebSocket live updates and the catch-up endpoint (real server)."""

import asyncio
import json
import socket
import time
import uuid

import pytest
import uvicorn
import websockets
from sqlalchemy import text

from app.core.db import get_sessionmaker
from app.platform.context import RequestInfo
from app.platform.uow import UnitOfWork
from tests.deep.phase1.kit import bearer, h, token_for
from tests.helpers import dispatch_all, headers

REQ = RequestInfo()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def server(database):
    from app.main import create_app

    port = _free_port()
    srv = uvicorn.Server(
        uvicorn.Config(
            create_app(), host="127.0.0.1", port=port, log_level="warning", lifespan="on"
        )
    )
    task = asyncio.create_task(srv.serve())
    for _ in range(200):
        if srv.started:
            break
        await asyncio.sleep(0.05)
    assert srv.started
    await asyncio.sleep(0.8)  # LISTEN attaches
    yield port
    srv.should_exit = True
    await asyncio.wait_for(task, 15)


def tok(actor) -> str:
    return headers(actor)["Authorization"].split(" ", 1)[1]


def connect(port, token):
    return websockets.connect(f"ws://127.0.0.1:{port}/ws?token={token}")


async def emit_for(actor_world, owner, scope_key="audit.view"):
    async with UnitOfWork(actor_world.admin, REQ) as uow:
        await uow.emit(
            "user.updated", {"user_id": str(owner.id)},
            rt={"entity_type": "user", "entity_id": str(owner.id), "owner_id": owner.id,
                "team_id": owner.team_id, "scope_key": scope_key},
        )  # fmt: skip


async def drain(ws, wait=0.7) -> list[dict]:
    out = []
    while True:
        try:
            out.append(json.loads(await asyncio.wait_for(ws.recv(), wait)))
        except TimeoutError:
            return out
        except websockets.exceptions.ConnectionClosed:
            return out


async def is_refused(port, token) -> int | None:
    try:
        async with connect(port, token):
            return None
    except websockets.exceptions.InvalidStatus as e:
        return e.response.status_code


# ------------------------------------------------------------------ handshake
async def test_bad_expired_forged_and_unknown_tokens_are_refused(
    server, world, world_b, admin_engine
):
    port = server
    for t in (
        "nope",
        "",
        token_for(world.sa.email, hours=-1),
        token_for(world.sa.email, secret="z" * 40),
        token_for("ghost@example.com"),
    ):
        assert await is_refused(port, t) == 403, t[:20]
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE users SET is_active=false WHERE id=:i"), {"i": world.north1.id}
        )
        await c.execute(text("UPDATE users SET team_id=NULL WHERE id=:i"), {"i": world.north2.id})
    assert await is_refused(port, tok(world.north1)) == 403  # deactivated
    assert await is_refused(port, tok(world.north2)) == 403  # not in a team
    # no token at all
    with pytest.raises(websockets.exceptions.InvalidStatus) as ei:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws"):
            pass
    assert ei.value.response.status_code in (403, 404, 422)
    # a good token works
    async with connect(port, tok(world.sa)) as ws:
        await ws.send("ping")
        assert await asyncio.wait_for(ws.recv(), 3) == "pong"


async def test_ping_pong_ignores_unknown_text_and_survives_garbage(server, world):
    async with connect(server, tok(world.tl_north)) as ws:
        await ws.send("hello")
        await ws.send("")
        await ws.send("x" * 100_000)
        await ws.send("ping")
        assert await asyncio.wait_for(ws.recv(), 3) == "pong"


async def test_binary_frame_does_not_kill_the_server(server, world):
    try:
        async with connect(server, tok(world.tl_north)) as ws:
            await ws.send(b"\x00\x01")
            await asyncio.sleep(0.3)
    except websockets.exceptions.ConnectionClosed:
        pass
    async with connect(server, tok(world.sa)) as ws2:  # the server still serves others
        await ws2.send("ping")
        assert await asyncio.wait_for(ws2.recv(), 3) == "pong"


# ------------------------------------------------------------------ fan-out by current scope
async def test_fan_out_by_scope_and_org(server, world, world_b):
    port = server
    async with (
        connect(port, tok(world.admin)) as admin, connect(port, tok(world.tl_north)) as tln, connect(port, tok(world.tl_south)) as tls,
        connect(port, tok(world.north1)) as n1, connect(port, tok(world.north2)) as n2, connect(port, tok(world_b.sa)) as b_sa,
    ):  # fmt: skip
        await asyncio.sleep(0.5)
        await emit_for(world, world.north1)
        await dispatch_all()
        got = {
            k: [e["entity_id"] for e in await drain(w)]
            for k, w in dict(admin=admin, tln=tln, tls=tls, n1=n1, n2=n2, b_sa=b_sa).items()
        }
    me = str(world.north1.id)
    assert got == {"admin": [me], "tln": [me], "tls": [], "n1": [me], "n2": [], "b_sa": []}


async def test_team_move_mid_connection_changes_who_receives_at_once(server, client, world):
    port = server
    async with (
        connect(port, tok(world.tl_north)) as tln,
        connect(port, tok(world.tl_south)) as tls,
        connect(port, tok(world.north1)) as n1,
    ):
        await asyncio.sleep(0.5)
        await emit_for(world, world.north1)
        await dispatch_all()
        assert [len(await drain(w)) for w in (tln, tls, n1)] == [1, 0, 1]
        # move north1 to South through the API
        r = await client.patch(
            f"/users/{world.north1.id}",
            json={"team_id": str(world.south_team), "reason": "re-org"},
            headers=h(world.admin),
        )
        assert r.status_code == 200
        await dispatch_all()
        await drain(tln), await drain(tls), await drain(n1)  # the move events themselves
        await emit_for(world, world.north1)
        await dispatch_all()
        assert [len(await drain(w)) for w in (tln, tls, n1)] == [0, 1, 1]


async def test_role_change_mid_connection_changes_scope_immediately(server, client, world):
    port = server
    # north2 is a plain User: sees only their own events. Make them Team Leader of an empty new team.
    team = (
        await client.post("/teams", json={"name": "Live team", "reason": "r"}, headers=h(world.sa))
    ).json()["id"]
    async with connect(port, tok(world.north2)) as n2:
        await asyncio.sleep(0.5)
        await emit_for(world, world.north1)
        await dispatch_all()
        assert await drain(n2) == []  # a User never sees a team mate's events
        await client.patch(
            f"/users/{world.north2.id}",
            json={"team_id": team, "reason": "r"},
            headers=h(world.admin),
        )
        await client.patch(
            f"/users/{world.north2.id}",
            json={"role": "team_leader", "reason": "r"},
            headers=h(world.admin),
        )
        await dispatch_all()
        await drain(n2)
        # now a TL of the new team: still nothing about north1 (another team) ...
        await emit_for(world, world.north1)
        await dispatch_all()
        assert await drain(n2) == []
        # ... but events about their own record arrive
        await emit_for(world, world.north2)
        await dispatch_all()
        assert len(await drain(n2)) == 1


async def test_deactivated_users_socket_is_closed(server, client, world):
    async with connect(server, tok(world.north1)) as n1:
        await asyncio.sleep(0.4)
        r = await client.patch(
            f"/users/{world.north1.id}",
            json={"is_active": False, "reason": "left"},
            headers=h(world.admin),
        )
        assert r.status_code == 200
        await dispatch_all()
        with pytest.raises(websockets.exceptions.ConnectionClosed):
            for _ in range(5):
                await asyncio.wait_for(n1.recv(), 2)


async def test_super_admin_owned_events_are_not_pushed_to_team_roles(server, world):
    async with (
        connect(server, tok(world.tl_north)) as tln,
        connect(server, tok(world.admin)) as adm,
    ):
        await asyncio.sleep(0.4)
        async with UnitOfWork(world.admin, REQ) as uow:
            await uow.emit(
                "deal.updated",
                {},
                rt={
                    "entity_type": "deal",
                    "entity_id": str(uuid.uuid4()),
                    "owner_id": world.sa.id,
                    "team_id": world.north_team,
                    "scope_key": "deal.view",
                },
            )
        await dispatch_all()
        assert await drain(tln) == [] and len(await drain(adm)) == 1


# ------------------------------------------------------------------ reconnect and catch-up
async def test_reconnect_resumes_without_gaps_or_duplicates(server, client, world):
    port = server
    t = tok(world.tl_north)
    seen: list[int] = []
    async with connect(port, t) as ws:
        await asyncio.sleep(0.4)
        for o in (world.north1, world.north2):
            await emit_for(world, o)
        await dispatch_all()
        seen += [e["id"] for e in await drain(ws)]
    assert len(seen) == 2
    for o in (world.north1, world.south1, world.tl_north, world.north2):  # while offline
        await emit_for(world, o)
    await dispatch_all()
    async with connect(port, t) as ws:
        await asyncio.sleep(0.4)
        missed = (await client.get(f"/events?since={seen[-1]}", headers=bearer(t))).json()
        await emit_for(world, world.north1)  # live again
        await dispatch_all()
        live = [e["id"] for e in await drain(ws)]
    ids = seen + [e["id"] for e in missed] + live
    assert len(missed) == 3  # north1, tl_north, north2 (south1 is another team)
    assert ids == sorted(ids) and len(ids) == len(set(ids))


async def test_catch_up_is_not_starved_by_a_long_run_of_invisible_events(
    client, world, admin_engine
):
    async with get_sessionmaker()() as s:
        last = (await s.execute(text("SELECT coalesce(max(id),0) FROM outbox_events"))).scalar_one()

    def rt(o):
        return json.dumps(
            {
                "rt": {
                    "entity_type": "user",
                    "entity_id": str(o.id),
                    "owner_id": str(o.id),
                    "team_id": str(o.team_id),
                    "scope_key": "audit.view",
                }
            }
        )

    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO outbox_events (org_id,type,payload,status) SELECT :o,'user.updated',CAST(:p AS jsonb),'done' FROM generate_series(1,250)"
            ),
            {"o": world.org_id, "p": rt(world.south1)},
        )
        await c.execute(
            text(
                "INSERT INTO outbox_events (org_id,type,payload,status) VALUES (:o,'user.updated',CAST(:p AS jsonb),'done')"
            ),
            {"o": world.org_id, "p": rt(world.north1)},
        )
    # the client only knows `last`; it pages with the last id it was given. It must reach the visible event.
    since, found = last, []
    for _ in range(5):
        got = (await client.get(f"/events?since={since}", headers=h(world.north1))).json()
        if not got:
            break
        found += got
        since = got[-1]["id"]
    assert [e["entity_id"] for e in found] == [str(world.north1.id)]


async def test_catch_up_returns_at_most_200_and_in_order(client, world, admin_engine):
    async with get_sessionmaker()() as s:
        last = (await s.execute(text("SELECT coalesce(max(id),0) FROM outbox_events"))).scalar_one()
    rt = json.dumps(
        {
            "rt": {
                "entity_type": "user",
                "entity_id": str(world.north1.id),
                "owner_id": str(world.north1.id),
                "team_id": str(world.north_team),
                "scope_key": "audit.view",
            }
        }
    )
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO outbox_events (org_id,type,payload,status) SELECT :o,'user.updated',CAST(:p AS jsonb),'done' FROM generate_series(1,230)"
            ),
            {"o": world.org_id, "p": rt},
        )
    got = (await client.get(f"/events?since={last}", headers=h(world.sa))).json()
    assert len(got) == 200 and [e["id"] for e in got] == sorted(e["id"] for e in got)
    nxt = (await client.get(f"/events?since={got[-1]['id']}", headers=h(world.sa))).json()
    assert len(nxt) == 30 and nxt[0]["id"] > got[-1]["id"]


# ------------------------------------------------------------------ token lifetime and load
async def test_socket_stops_receiving_after_the_token_expires(server, world):
    short = token_for(world.north1.email, hours=3 / 3600)  # valid for 3 seconds
    async with connect(server, short) as ws:
        await asyncio.sleep(4.5)
        await emit_for(world, world.north1)
        await dispatch_all()
        try:
            got = await drain(ws, 1.5)
        except websockets.exceptions.ConnectionClosed:
            got = []
    assert got == []


async def test_fan_out_cost_with_many_connections(server, world):
    """RISK numbers: every event costs several queries per connected socket."""
    port = server
    actors = [world.north1, world.north2, world.tl_north, world.south1, world.admin]
    socks = []
    try:
        for i in range(60):
            socks.append(await connect(port, tok(actors[i % len(actors)])).__aenter__())
        await asyncio.sleep(0.5)
        t0 = time.perf_counter()
        await emit_for(world, world.north1)
        await dispatch_all()
        first = None
        while first is None:
            try:
                await asyncio.wait_for(socks[0].recv(), 5)
                first = time.perf_counter() - t0
            except TimeoutError:
                first = -1
        await asyncio.sleep(1.5)
        total = time.perf_counter() - t0
        print(
            f"LIVE: 60 sockets, one event: first push after {first:.2f}s, all within {total:.2f}s"
        )
        assert first < 5
    finally:
        for s in socks:
            await s.close()
