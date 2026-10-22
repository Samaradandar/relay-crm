# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.errors import Conflict, PermissionDenied, ReasonRequired, register_error_handlers


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_error_format():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/denied")
    async def denied():
        raise PermissionDenied("nope", {"key": "deal.edit"})

    @app.get("/reason")
    async def reason():
        raise ReasonRequired("reason needed")

    @app.get("/conflict")
    async def conflict():
        raise Conflict()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/denied")
        assert r.status_code == 403
        assert r.json() == {
            "code": "permission_denied",
            "message": "nope",
            "details": {"key": "deal.edit"},
        }
        r = await c.get("/reason")
        assert r.status_code == 422 and r.json()["code"] == "reason_required"
        assert (await c.get("/conflict")).status_code == 409
        r404 = await c.get("/missing")
        assert r404.status_code == 404 and r404.json()["code"] == "http_error"


async def test_ready_checks_the_database(client, monkeypatch):
    r = await client.get("/health/ready")
    assert r.status_code == 200 and r.json() == {"status": "ok", "database": "ok"}

    def down():
        raise ConnectionError("database is down")

    import app.main as main

    monkeypatch.setattr(main, "get_sessionmaker", down)
    r = await client.get("/health/ready")
    assert r.status_code == 503 and r.json()["database"] == "down"
    assert (await client.get("/health")).status_code == 200  # the process itself is still up
