# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 1 deep test, section 12: migrations, backup and restore, configuration, error format, abuse."""

import asyncio
import json
import time
import uuid
from pathlib import Path

import asyncpg
import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.core import security
from app.core.config import Settings, get_settings
from app.main import create_app
from app.platform.audit import service as audit_service
from app.platform.uow import UnitOfWork
from scripts import backup_restore
from tests.deep.phase1.kit import audit_rows, bearer, h, new_email, token_for

API_DIR = Path(__file__).resolve().parents[3]


async def scratch_db(database, name_prefix="deep"):
    raw_admin = database.admin_url.replace("postgresql+asyncpg://", "postgresql://")
    base = raw_admin.rsplit("/", 1)[0]
    name = f"{name_prefix}_{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(base + "/postgres")
    await conn.execute(f'CREATE DATABASE "{name}"')
    await conn.close()
    return base, name


# ------------------------------------------------------------------ migrations
async def test_empty_database_migrates_to_head_twice_and_matches_the_template(
    database, admin_engine
):
    base, name = await scratch_db(database, "mig")
    url = base.replace("postgresql://", "postgresql+asyncpg://") + "/" + name
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    await asyncio.to_thread(command.upgrade, cfg, "head")  # a second run changes nothing
    eng = create_async_engine(url)
    try:
        async with eng.connect() as c:
            tables = {
                r[0]
                for r in (
                    await c.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
                        )
                    )
                ).all()
            }
            n_keys = (await c.execute(text("SELECT count(*) FROM permission_keys"))).scalar_one()
            n_rows = (await c.execute(text("SELECT count(*) FROM role_permissions"))).scalar_one()
            ver = (await c.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
        async with admin_engine.connect() as c:
            tmpl = {
                r[0]
                for r in (
                    await c.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name NOT LIKE 'procrastinate%'"
                        )
                    )
                ).all()
            }
            tmpl_rows = (
                await c.execute(text("SELECT count(*) FROM role_permissions"))
            ).scalar_one()
        assert {
            t for t in tables if not t.startswith("procrastinate")
        } == tmpl and n_rows == tmpl_rows == 4 * n_keys
        assert ver
    finally:
        await eng.dispose()


async def test_grants_can_be_run_twice_and_audit_log_stays_protected(database, admin_engine):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "m0001", API_DIR / "alembic" / "versions" / "0001_phase1_base.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    async def priv():
        async with admin_engine.connect() as c:
            return sorted(
                (
                    await c.execute(
                        text(
                            "SELECT table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee='relay_app' AND table_name IN ('audit_log','users','outbox_events') ORDER BY 1,2"
                        )
                    )
                ).all()
            )

    before = await priv()
    for _ in range(2):
        async with admin_engine.begin() as c:
            for stmt in mod.GRANTS:
                await c.execute(text(stmt))
    after = await priv()
    assert after == before
    audit = {p for t, p in after if t == "audit_log"}
    assert (
        audit == {"INSERT", "SELECT"}
        or audit >= {"INSERT", "SELECT"}
        and not (audit & {"UPDATE", "DELETE", "TRUNCATE"})
    )


# ------------------------------------------------------------------ backup / restore
async def test_backup_and_restore_keep_every_table_the_chain_the_guards_and_the_sequences(
    world, world_b, database, tmp_path
):
    async with UnitOfWork(world.admin) as uow:
        for i in range(5):
            await uow.audit("deal.edit", "widget", f"bk{i}", new={"i": i}, reason="before backup")
            await uow.emit("deep.backup", {"n": i})
    async with UnitOfWork(world_b.admin) as uow:
        await uow.audit("deal.edit", "widget", "other-org", new={"i": 1}, reason="r")
    raw_admin = database.admin_url.replace("postgresql+asyncpg://", "postgresql://")
    manifest = backup_restore.backup(raw_admin, tmp_path / "bk", bucket=None)
    assert manifest["files"] == 0
    base, name = await scratch_db(database, "restored")
    result = backup_restore.restore(tmp_path / "bk", f"{base}/{name}", bucket=None)
    assert result["database"] == "restored"
    # restoring again into the same, now non-empty database must fail loudly, not half-merge
    import subprocess

    with pytest.raises(subprocess.CalledProcessError):
        backup_restore.restore(tmp_path / "bk", f"{base}/{name}", bucket=None)

    src = await asyncpg.connect(raw_admin)
    dst = await asyncpg.connect(f"{base}/{name}")
    try:
        tables = [
            r["table_name"]
            for r in await src.fetch(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1"
            )
        ]
        assert "audit_log" in tables and "users" in tables
        for t in tables:
            a = await src.fetchval(f'SELECT count(*) FROM "{t}"')  # noqa: S608
            b = await dst.fetchval(f'SELECT count(*) FROM "{t}"')  # noqa: S608
            assert a == b, (t, a, b)
        # same data in the audit table, byte for byte
        q = "SELECT md5(string_agg(row_hash || prev_hash || seq::text, ',' ORDER BY seq)) FROM audit_log"
        assert await src.fetchval(q) == await dst.fetchval(q)
        # triggers and the append-only guard survive
        trg = await dst.fetch(
            "SELECT tgname FROM pg_trigger WHERE tgrelid='audit_log'::regclass AND NOT tgisinternal"
        )
        assert {r["tgname"] for r in trg} == {"audit_log_no_update_delete", "audit_log_no_truncate"}
        with pytest.raises(asyncpg.PostgresError):
            await dst.execute("UPDATE audit_log SET reason='x'")
        # the app role keeps its limited rights in the restored copy
        privs = {
            (r[0], r[1])
            for r in await dst.fetch(
                "SELECT table_name, privilege_type FROM information_schema.role_table_grants WHERE grantee='relay_app' AND table_name='audit_log'"
            )
        }
        assert (
            ("audit_log", "UPDATE") not in privs
            and ("audit_log", "DELETE") not in privs
            and ("audit_log", "INSERT") in privs
        )
        # sequences continue after the highest value: a new audit row cannot collide
        mx = await dst.fetchval(
            "SELECT max(seq) FROM audit_log WHERE seq < 1000000"
        )  # (a tamper test shifts some seqs)
        nxt = await dst.fetchval("SELECT nextval(pg_get_serial_sequence('audit_log','seq'))")
        assert nxt > mx
        # chain valid for every org in the restored copy
        eng = create_async_engine(f"postgresql+asyncpg://{base.split('://', 1)[1]}/{name}")
        try:
            async with eng.connect() as c:
                from sqlalchemy.ext.asyncio import AsyncSession

                async with AsyncSession(bind=c) as s:
                    for org in (world.org_id, world_b.org_id):
                        res = await audit_service.verify_chain(s, org)
                        assert res.ok and res.checked >= 1
        finally:
            await eng.dispose()
    finally:
        await src.close()
        await dst.close()


async def test_restore_without_manifest_or_with_a_corrupt_dump_fails_loudly(database, tmp_path):
    base, name = await scratch_db(database, "badrestore")
    with pytest.raises(FileNotFoundError):
        backup_restore.restore(tmp_path / "nothing", f"{base}/{name}", bucket=None)
    (tmp_path / "bk").mkdir()
    (tmp_path / "bk" / "db.dump").write_bytes(b"not a dump")
    (tmp_path / "bk" / "manifest.json").write_text(json.dumps({"dump": "db.dump"}))
    import subprocess

    with pytest.raises(subprocess.CalledProcessError):
        backup_restore.restore(tmp_path / "bk", f"{base}/{name}", bucket=None)


# ------------------------------------------------------------------ configuration
def _prod(monkeypatch, **kw):
    s = Settings(_env_file=None, env="production", **kw)
    monkeypatch.setattr(security, "get_settings", lambda: s)
    security.get_auth_provider.cache_clear()
    return s


def test_dev_provider_refuses_production_when_first_used(monkeypatch):
    try:
        _prod(monkeypatch, auth_provider="dev")
        with pytest.raises(RuntimeError, match="production"):
            security.get_auth_provider()
        _prod(monkeypatch, auth_provider="cognito")  # no pool id / client id
        with pytest.raises(RuntimeError):
            security.get_auth_provider()
        _prod(
            monkeypatch,
            auth_provider="cognito",
            cognito_user_pool_id="pool",
            cognito_client_id="cid",
        )
        assert isinstance(security.get_auth_provider(), security.CognitoProvider)
    finally:
        monkeypatch.undo()
        security.get_auth_provider.cache_clear()


async def test_app_refuses_to_start_in_production_with_the_dev_provider(monkeypatch):
    s = Settings(_env_file=None, env="production", auth_provider="dev")
    monkeypatch.setattr("app.main.get_settings", lambda: s)
    with pytest.raises(Exception):  # noqa: B017, PT011
        app = create_app()
        async with app.router.lifespan_context(app):
            pass


def test_the_defaults_are_the_insecure_development_ones():
    """RISK evidence: with no ENV the app is 'local' with the dev provider, a published JWT secret and
    a login endpoint that mints a token for any existing e-mail."""
    f = Settings.model_fields
    assert f["env"].default == "local" and f["auth_provider"].default == "dev"
    assert f["dev_jwt_secret"].default == "local-dev-secret-change-me-32-bytes-min"
    assert f["database_url"].default.startswith("postgresql+asyncpg://relay_app:relay_app@")


async def test_cors_allows_only_the_configured_origins(database, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://crm.example.com, https://admin.example.com")
    get_settings.cache_clear()
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            for origin, ok in (
                ("https://crm.example.com", True),
                ("https://admin.example.com", True),
                ("https://evil.example.com", False),
                ("http://crm.example.com", False),
                ("null", False),
                ("https://crm.example.com.evil.com", False),
            ):
                r = await c.get("/health", headers={"Origin": origin})
                got = r.headers.get("access-control-allow-origin")
                assert (got == origin) if ok else (got is None), (origin, got)
                pre = await c.options(
                    "/me",
                    headers={
                        "Origin": origin,
                        "Access-Control-Request-Method": "GET",
                        "Access-Control-Request-Headers": "authorization",
                    },
                )
                assert (
                    (pre.headers.get("access-control-allow-origin") == origin)
                    if ok
                    else (pre.headers.get("access-control-allow-origin") is None)
                )
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# ------------------------------------------------------------------ error format
async def test_error_bodies_use_one_envelope_and_never_leak_internals(client, world):
    A = h(world.admin)
    samples = [
        await client.get("/nope"),
        await client.get("/me", headers=bearer("x")),
        await client.delete("/me", headers=A),
        await client.post("/users", json={}, headers=A),
        await client.get("/approvals/xyz", headers=A),
        await client.patch(f"/users/{world.north1.id}", json={"role": "god"}, headers=A),
        await client.patch(
            f"/users/{world.south1.id}", json={"name": "x", "reason": "r"}, headers=h(world.north1)
        ),
        await client.get(f"/users/{uuid.uuid4()}", headers=A),
        await client.post(
            "/users", content=b"\xff\xfe", headers={**A, "content-type": "application/json"}
        ),
    ]
    for r in samples:
        assert r.status_code >= 400
        body = r.json()
        assert set(body) == {"code", "message", "details"}, (r.status_code, body)
        assert (
            isinstance(body["code"], str)
            and isinstance(body["message"], str)
            and isinstance(body["details"], dict)
        )
        low = r.text.lower()
        for bad in (
            "traceback",
            "sqlalchemy",
            "asyncpg",
            "select ",
            "insert into",
            "psycopg",
            'file "',
            "c:\\\\",
        ):
            assert bad not in low, (r.status_code, bad)


async def test_unhandled_errors_also_use_the_envelope(world):
    app = create_app()

    @app.get("/boom-for-test")
    async def boom() -> None:
        raise RuntimeError("secret detail")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/boom-for-test")
    assert r.status_code == 500
    assert set(r.json()) == {"code", "message", "details"}
    assert "secret detail" not in r.text


async def test_every_post_and_patch_route_answers_bad_bodies_in_the_envelope(client, world):
    schema = create_app().openapi()
    checked = 0
    for path, item in schema["paths"].items():
        for method, op in item.items():
            if method.upper() not in ("POST", "PATCH", "PUT") or "requestBody" not in op:
                continue
            url = path
            while "{" in url:
                a = url.index("{")
                b = url.index("}", a)
                url = url[:a] + str(uuid.uuid4()) + url[b + 1 :]
            r = await client.request(
                method.upper(),
                url,
                content=b'{"__bad__": [1,2,',
                headers={**h(world.sa), "content-type": "application/json"},
            )
            assert r.status_code in (400, 404, 422), (method, path, r.status_code)
            assert set(r.json()) == {"code", "message", "details"}, (method, path, r.text[:120])
            checked += 1
    assert checked > 15


async def test_openapi_and_docs_are_public(client):
    """RISK (S4): the full API description, including later phases, is readable without a token."""
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert (await client.get(path)).status_code == 200


# ------------------------------------------------------------------ abuse (numbers for the report)
async def test_one_thousand_rapid_logins_numbers(client, world):
    t0 = time.perf_counter()
    tasks = []
    for i in range(1000):
        email = world.north1.email if i % 2 == 0 else f"ghost{i}@example.com"
        tasks.append(client.post("/auth/dev/login", json={"email": email}))
    rs = []
    for i in range(0, 1000, 100):
        rs += await asyncio.gather(*tasks[i : i + 100])
    dt = time.perf_counter() - t0
    codes = {c: sum(1 for r in rs if r.status_code == c) for c in {r.status_code for r in rs}}
    rows = await audit_rows(world.org_id, action_key="auth.login")
    print(
        f"LOGIN FLOOD: 1000 attempts in {dt:.1f}s ({1000 / dt:.0f}/s), codes={codes}, audit rows={len(rows)}"
    )
    assert set(codes) == {200, 401}  # no throttling, no errors
    assert codes[200] == 500 and len(rows) == 500


async def test_large_bodies_numbers(client, world):
    A = h(world.admin)
    for mb in (1, 10):
        reason = "r" * (mb * 1024 * 1024)
        t0 = time.perf_counter()
        r = await client.patch(
            f"/users/{world.north2.id}", json={"name": f"Big {mb}", "reason": reason}, headers=A
        )
        dt = time.perf_counter() - t0
        print(f"BIG BODY: {mb} MB reason -> HTTP {r.status_code} in {dt:.1f}s")
        assert r.status_code in (200, 413, 422)
    t0 = time.perf_counter()
    r = await client.post(
        "/auth/dev/login",
        content=b'{"email": "' + b"a" * (10 * 1024 * 1024) + b'"}',
        headers={"content-type": "application/json"},
    )
    print(
        f"BIG BODY: 10 MB login e-mail -> HTTP {r.status_code} in {time.perf_counter() - t0:.1f}s"
    )
    assert r.status_code in (401, 413, 422)


async def test_oversized_reason_is_refused(client, world):
    r = await client.patch(
        f"/users/{world.north2.id}",
        json={"name": "Big", "reason": "r" * (2 * 1024 * 1024)},
        headers=h(world.admin),
    )
    assert r.status_code in (413, 422)


async def test_new_email_helper_and_token_helper_are_sane():
    assert new_email() != new_email() and token_for("a@b.c").count(".") == 2
