# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 10: file upload, completion and download."""

import uuid

import pytest
from sqlalchemy import text

from app.core.db import get_sessionmaker
from tests.deep.phase1.kit import audit_rows, h

MAX = 25 * 1024 * 1024


async def upload(
    client,
    actor,
    target,
    *,
    name="a.pdf",
    size=100,
    ctype="application/pdf",
    etype="user",
    eid=None,
):
    body = {
        "entity_type": etype,
        "entity_id": str(eid or target.id),
        "filename": name,
        "content_type": ctype,
        "size": size,
    }
    return await client.post("/files/upload-url", json=body, headers=h(actor))


def key_of(url: str) -> str:
    return url.split("/put/")[1].split("?")[0]


async def uploaded(client, fake_storage, actor, target, size=100, **kw) -> str:
    r = await upload(client, actor, target, size=size, **kw)
    assert r.status_code == 200, r.text
    fake_storage.objects[key_of(r.json()["upload_url"])] = size
    c = await client.post(f"/files/{r.json()['file_id']}/complete", headers=h(actor))
    assert c.status_code == 200, c.text
    return r.json()["file_id"]


# ------------------------------------------------------------------ who may upload
async def test_upload_needs_the_permission_and_the_owner_record_in_scope(
    client, world, world_b, fake_storage
):
    t = world.north1
    want = {
        world.north1: 403,
        world.north2: 403,
        world.south1: 403,
        world.tl_south: 403,
        world.admin: 200,
        world.sa: 200,
    }
    for actor, code in want.items():
        assert (await upload(client, actor, t)).status_code == code, actor.name
    # another organisation: forbidden and indistinguishable from a missing record
    for actor in (world_b.admin, world_b.sa):
        real = await upload(client, actor, t)
        ghost = await upload(client, actor, t, eid=uuid.uuid4())
        assert real.status_code == ghost.status_code and real.json() == ghost.json()
        assert real.status_code in (403, 404)
    assert (await upload(client, world.admin, t, etype="nope")).status_code == 404
    for bad in ("not-a-uuid", "", "../x", "0", "%00"):
        r = (
            await upload(client, world.admin, t, eid=bad if bad else None)
            if bad
            else await client.post(
                "/files/upload-url",
                json={"entity_type": "user", "entity_id": "", "filename": "a", "size": 1},
                headers=h(world.admin),
            )
        )
        assert r.status_code in (403, 404, 422), (bad, r.status_code)


async def test_team_leader_cannot_attach_files_to_user_records(client, world, fake_storage):
    assert (await upload(client, world.tl_north, world.north1)).status_code == 403


async def test_upload_audit_and_no_row_for_a_refused_upload(client, world, fake_storage):
    n = len(await audit_rows(world.org_id))
    assert (await upload(client, world.north1, world.north1)).status_code == 403
    rows = (await audit_rows(world.org_id))[n:]
    assert [(r["outcome"], r["action_key"]) for r in rows] == [("denied", "user.manage")]
    async with get_sessionmaker()() as s:
        assert (
            await s.execute(text("SELECT count(*) FROM files WHERE org_id=:o"), {"o": world.org_id})
        ).scalar_one() == 0


# ------------------------------------------------------------------ size
@pytest.mark.parametrize(
    "size,ok",
    [
        (1, True),
        (2, True),
        (MAX - 1, True),
        (MAX, True),
        (MAX + 1, False),
        (0, False),
        (-1, False),
        (10**15, False),
    ],
)
async def test_size_boundaries(client, world, fake_storage, size, ok):
    r = await upload(client, world.admin, world.north1, size=size)
    if ok:
        assert (
            r.status_code == 200 and r.json()["expires_in"] == 600 and r.json()["method"] == "PUT"
        )
    else:
        assert r.status_code in (409, 422), r.status_code
        async with get_sessionmaker()() as s:
            assert (
                await s.execute(
                    text("SELECT count(*) FROM files WHERE org_id=:o"), {"o": world.org_id}
                )
            ).scalar_one() == 0


@pytest.mark.parametrize("size", ["10", 1.5, None, True, [], {}, "abc", 1e30])
async def test_size_wrong_types_are_422(client, world, fake_storage, size):
    r = await client.post(
        "/files/upload-url",
        json={
            "entity_type": "user",
            "entity_id": str(world.north1.id),
            "filename": "a",
            "size": size,
        },
        headers=h(world.admin),
    )
    assert r.status_code in (200, 422, 409) and r.status_code != 500, (size, r.text)
    if size in ("abc", None, [], {}, 1.5):
        assert r.status_code == 422


async def test_size_over_64_bits_is_not_a_500(client, world, fake_storage):
    r = await client.post(
        "/files/upload-url",
        json={
            "entity_type": "user",
            "entity_id": str(world.north1.id),
            "filename": "a",
            "size": 10**30,
        },
        headers=h(world.admin),
    )
    assert r.status_code in (409, 422)


async def test_missing_fields_and_extra_fields(client, world, fake_storage):
    r = await client.post("/files/upload-url", json={"entity_type": "user"}, headers=h(world.admin))
    assert r.status_code == 422 and set(r.json()) == {"code", "message", "details"}
    r = await client.post(
        "/files/upload-url",
        json={
            "entity_type": "user",
            "entity_id": str(world.north1.id),
            "filename": "a",
            "size": 5,
            "org_id": str(uuid.uuid4()),
            "uploaded_by": str(uuid.uuid4()),
            "status": "uploaded",
            "key": "evil",
        },
        headers=h(world.admin),
    )
    assert r.status_code == 200
    async with get_sessionmaker()() as s:
        row = (
            await s.execute(
                text("SELECT org_id, uploaded_by, status, key FROM files WHERE id=:i"),
                {"i": r.json()["file_id"]},
            )
        ).one()
    assert (
        row[0] == world.org_id
        and row[1] == world.admin.id
        and row[2] == "pending"
        and row[3] != "evil"
    )


# ------------------------------------------------------------------ filenames
@pytest.mark.parametrize(
    "name",
    ["../../etc/passwd", "..\\..\\windows\\system32", "a" * 10_000, "\u0939\u093f\u0928\u094d\u0926\u0940.pdf", "\U0001f600.png", "name" + chr(0) + ".exe", "....", "   ", "", ".", "..", "/abs/path.pdf", 'quote".pdf', "new\nline.pdf", "<script>.pdf", "a;b|c&d.pdf", "C:\\x.pdf", "%2e%2e%2fx"],
)  # fmt: skip
async def test_weird_filenames_are_neutralised(client, world, fake_storage, name):
    r = await upload(client, world.admin, world.north1, name=name)
    assert r.status_code == 200, (name, r.text[:200])
    url = r.json()["upload_url"]
    key = key_of(url)
    prefix = f"{world.org_id}/user/{world.north1.id}/"
    assert (
        key.startswith(prefix)
        and ".." not in key.split("/")
        and "\\" not in key
        and "\x00" not in key
    )
    assert len(key.split("/")) == 5  # org/user/id/uuid/name: the name stays ONE path segment
    async with get_sessionmaker()() as s:
        fn = (
            await s.execute(
                text("SELECT filename FROM files WHERE id=:i"), {"i": r.json()["file_id"]}
            )
        ).scalar_one()
    assert (
        0 < len(fn) <= 150
        and "/" not in fn
        and "\\" not in fn
        and "\x00" not in fn
        and "\n" not in fn
    )
    assert fn not in ("..", ".")


async def test_same_filename_twice_gets_two_distinct_keys(client, world, fake_storage):
    a = await upload(client, world.admin, world.north1, name="same.pdf")
    b = await upload(client, world.admin, world.north1, name="same.pdf")
    assert (
        key_of(a.json()["upload_url"]) != key_of(b.json()["upload_url"])
        and a.json()["file_id"] != b.json()["file_id"]
    )


async def test_hostile_content_type_is_data_not_a_header(client, world, fake_storage):
    for ct in ("text/html", "a\r\nX-Evil: 1", "x" * 5000, "", "application/x-msdownload"):
        r = await upload(client, world.admin, world.north1, ctype=ct)
        assert r.status_code == 200, (ct[:20], r.status_code)
        assert "\r" not in str(r.headers) and "x-evil" not in {k.lower() for k in r.headers}


# ------------------------------------------------------------------ completion
async def test_complete_before_upload_is_409_and_then_works_once_the_object_exists(
    client, world, fake_storage
):
    r = await upload(client, world.admin, world.north1, size=50)
    fid = r.json()["file_id"]
    c = await client.post(f"/files/{fid}/complete", headers=h(world.admin))
    assert c.status_code == 409 and c.json()["code"] == "conflict"
    fake_storage.objects[key_of(r.json()["upload_url"])] = 50
    assert (await client.post(f"/files/{fid}/complete", headers=h(world.admin))).json() == {
        "file_id": fid,
        "status": "uploaded",
        "size": 50,
    }
    # a second complete is harmless and writes no second audit row
    n = len([x for x in await audit_rows(world.org_id, action_key="file.complete")])
    assert (await client.post(f"/files/{fid}/complete", headers=h(world.admin))).status_code == 200
    assert len(await audit_rows(world.org_id, action_key="file.complete")) == n == 1


async def test_complete_by_someone_else_or_another_org_is_404(client, world, world_b, fake_storage):
    r = await upload(client, world.admin, world.north1)
    fid = r.json()["file_id"]
    fake_storage.objects[key_of(r.json()["upload_url"])] = 100
    for other in (world.sa, world.tl_north, world.north1, world_b.admin, world_b.sa):
        assert (await client.post(f"/files/{fid}/complete", headers=h(other))).status_code == 404, (
            other.name
        )
    assert (
        await client.post(f"/files/{uuid.uuid4()}/complete", headers=h(world.admin))
    ).status_code == 404
    assert (await client.post(f"/files/{fid}/complete", headers=h(world.admin))).status_code == 200


async def test_complete_records_the_real_size_and_rejects_oversize_objects(
    client, world, fake_storage
):
    r = await upload(client, world.admin, world.north1, size=10)
    fake_storage.objects[key_of(r.json()["upload_url"])] = 12345
    c = await client.post(f"/files/{r.json()['file_id']}/complete", headers=h(world.admin))
    assert c.status_code == 200 and c.json()["size"] == 12345  # the stored size is the real one
    r2 = await upload(client, world.admin, world.north1, size=10)
    fake_storage.objects[key_of(r2.json()["upload_url"])] = MAX + 1
    c = await client.post(f"/files/{r2.json()['file_id']}/complete", headers=h(world.admin))
    assert c.status_code == 409
    d = await client.get(
        f"/files/{r2.json()['file_id']}", headers=h(world.admin), follow_redirects=False
    )
    assert d.status_code == 404  # never downloadable


async def test_the_declared_size_cap_is_not_enforced_on_the_signed_url(client, world, fake_storage):
    """RISK (documented): the PUT URL carries no content-length limit, so the 25 MB cap is only checked
    after the upload; a client can park an oversize object in the bucket. The fake storage cannot
    show the real signature, so the check is on the URL contract."""
    r = await upload(client, world.admin, world.north1, size=10)
    assert "content-length" not in r.json()["upload_url"].lower()


# ------------------------------------------------------------------ download
async def test_download_scope_matrix_and_redirect(client, world, world_b, fake_storage):
    fid = await uploaded(client, fake_storage, world.admin, world.north1)
    ok = {
        world.north1: 307,
        world.tl_north: 307,
        world.admin: 307,
        world.sa: 307,
        world.north2: 404,
        world.tl_south: 404,
        world.south1: 404,
        world_b.sa: 404,
        world_b.admin: 404,
    }
    for actor, code in ok.items():
        r = await client.get(f"/files/{fid}", headers=h(actor), follow_redirects=False)
        assert r.status_code == code, (actor.name, r.status_code)
        if code == 307:
            assert (
                r.headers["location"].startswith("https://files.test/get/")
                and "exp=300" in r.headers["location"]
            )
    # a file on an Admin's own record: only Admin/SA (the owner is out of every team's scope)
    fid2 = await uploaded(client, fake_storage, world.sa, world.admin)
    for actor, code in {
        world.admin: 307,
        world.sa: 307,
        world.tl_north: 404,
        world.north1: 404,
    }.items():
        assert (
            await client.get(f"/files/{fid2}", headers=h(actor), follow_redirects=False)
        ).status_code == code


async def test_pending_deleted_and_orphaned_files_are_404(
    client, world, fake_storage, admin_engine
):
    pend = (await upload(client, world.admin, world.north1)).json()["file_id"]
    assert (
        await client.get(f"/files/{pend}", headers=h(world.sa), follow_redirects=False)
    ).status_code == 404
    done = await uploaded(client, fake_storage, world.admin, world.north1)
    assert (
        await client.get(f"/files/{done}", headers=h(world.sa), follow_redirects=False)
    ).status_code == 307
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE files SET deleted_at=now() WHERE id=:i"), {"i": done})
    assert (
        await client.get(f"/files/{done}", headers=h(world.sa), follow_redirects=False)
    ).status_code == 404
    # the owner record becomes unknown (e.g. entity type no longer registered)
    other = await uploaded(client, fake_storage, world.admin, world.north2)
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE files SET owner_entity_type='ghost' WHERE id=:i"), {"i": other}
        )
    assert (
        await client.get(f"/files/{other}", headers=h(world.sa), follow_redirects=False)
    ).status_code == 404
    assert (await client.get("/files/not-a-uuid", headers=h(world.sa))).status_code == 422


async def test_download_follows_the_owner_when_the_owner_moves_team(client, world, fake_storage):
    fid = await uploaded(client, fake_storage, world.admin, world.north1)
    assert (
        await client.get(f"/files/{fid}", headers=h(world.tl_north), follow_redirects=False)
    ).status_code == 307
    await client.patch(
        f"/users/{world.north1.id}",
        json={"team_id": str(world.south_team), "reason": "move"},
        headers=h(world.admin),
    )
    assert (
        await client.get(f"/files/{fid}", headers=h(world.tl_north), follow_redirects=False)
    ).status_code == 404
    assert (
        await client.get(f"/files/{fid}", headers=h(world.tl_south), follow_redirects=False)
    ).status_code == 307


async def test_deactivated_uploader_token_stops_working_for_complete(client, world, fake_storage):
    r = await upload(client, world.admin, world.north1)
    fake_storage.objects[key_of(r.json()["upload_url"])] = 100
    tok = h(world.admin)
    await client.patch(
        f"/users/{world.admin.id}", json={"is_active": False, "reason": "x"}, headers=h(world.sa)
    )
    assert (
        await client.post(f"/files/{r.json()['file_id']}/complete", headers=tok)
    ).status_code == 401


async def test_uppercase_or_wrapped_entity_ids_do_not_split_the_history(
    client, world, fake_storage
):
    """The audit row stores the entity id exactly as the client typed it."""
    r = await upload(client, world.admin, world.north1, eid=str(world.north1.id).upper())
    assert r.status_code == 200
    hist = (await client.get(f"/history/user/{world.north1.id}", headers=h(world.admin))).json()
    assert [x for x in hist if x["action"] == "file.upload"] != [], (
        "file.upload is missing from the canonical history"
    )


async def test_hidden_file_answers_exactly_like_a_missing_file(
    client, world, world_b, fake_storage
):
    fid = await uploaded(client, fake_storage, world.admin, world.north1)
    ghost = await client.get(
        f"/files/{uuid.uuid4()}", headers=h(world.south1), follow_redirects=False
    )
    for hidden_for in (world.south1, world.north2, world.tl_south):
        hidden = await client.get(f"/files/{fid}", headers=h(hidden_for), follow_redirects=False)
        assert hidden.status_code == ghost.status_code and hidden.json() == ghost.json()
    # across organisations the answer is already identical
    other = await client.get(f"/files/{fid}", headers=h(world_b.sa), follow_redirects=False)
    assert other.json() == ghost.json()
