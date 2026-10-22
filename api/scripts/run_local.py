# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Run the whole backend WITHOUT Docker: embedded PostgreSQL 16 + migrations + seed + API + worker.

    python scripts/run_local.py            # API on http://localhost:8000
    python scripts/run_local.py --reset    # wipe the local database first
    (if a reset fails because files are locked, use --data <new-name>)

Data lives in api/.localdata. Uploaded files are kept in api/.localdata/<data>-files (no MinIO
needed). Email is not available in this mode: emails are discarded. Use docker compose for the
full stack.
"""

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

import asyncpg
import pgserver
import uvicorn
from alembic.config import Config

from alembic import command

if sys.platform == "win32":  # psycopg (used by the job queue) needs the selector loop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))


async def main(reset: bool, port: int, name: str, load: int = 0, demo: bool = False) -> None:
    data = API_DIR / ".localdata" / name
    if reset and data.exists():
        shutil.rmtree(data, ignore_errors=True)
    data.parent.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(data, cleanup_mode=None)
    raw = server.get_uri()
    creds_host = raw.split("://", 1)[1].rsplit("/", 1)[0]
    host = creds_host.rsplit("@", 1)[1]
    conn = await asyncpg.connect(raw)
    try:
        if not await conn.fetchval("select 1 from pg_roles where rolname='relay_app'"):
            await conn.execute("CREATE ROLE relay_app LOGIN PASSWORD 'relay_app'")
        if not await conn.fetchval("select 1 from pg_database where datname='relay'"):
            await conn.execute("CREATE DATABASE relay")
    finally:
        await conn.close()

    os.environ["MIGRATION_DATABASE_URL"] = f"postgresql+asyncpg://postgres:@{host}/relay"
    os.environ["DATABASE_URL"] = f"postgresql+asyncpg://relay_app:relay_app@{host}/relay"
    os.environ.setdefault("ENV", "local")
    os.environ.setdefault("AUTH_PROVIDER", "dev")
    # No MinIO here: keep uploaded files in a folder, served by the API's signed /files/local/ links
    os.environ.setdefault("LOCAL_STORAGE_DIR", str(API_DIR / ".localdata" / f"{name}-files"))
    os.environ.setdefault("LOCAL_STORAGE_URL", f"http://localhost:{port}")

    from app.core.config import get_settings

    get_settings.cache_clear()
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", os.environ["MIGRATION_DATABASE_URL"])
    await asyncio.to_thread(command.upgrade, cfg, "head")

    from app.platform.jobs.schema import apply_schema

    await apply_schema()
    from scripts.seed_dev import seed

    await seed()
    if demo:  # made-up companies so every screen has something to show
        from scripts.seed_demo import main as seed_demo

        await seed_demo(dispose=False)
    if load:  # bulk sample deals for scripts/loadtest.py
        from scripts.loadtest import seed as seed_load

        await seed_load(load)

    from app.platform import registry
    from app.platform.jobs.app import app as jobs_app
    from app.worker import outbox_loop

    registry.setup()
    stop = asyncio.Event()
    outbox_task = asyncio.create_task(outbox_loop(stop))
    config = uvicorn.Config("app.main:app", host="127.0.0.1", port=port, log_level="info")
    api = uvicorn.Server(config)
    print(f"\nRelay CRM API: http://localhost:{port}/docs   (Ctrl+C to stop)\n")
    try:
        async with jobs_app.open_async():
            await api.serve()
    finally:
        stop.set()
        await outbox_task
        server.cleanup()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--data", default="pg", help="folder name under .localdata")
    ap.add_argument("--demo", action="store_true", help="also add demo data (made-up companies)")
    ap.add_argument("--load", type=int, default=0, help="also create N sample open deals")
    args = ap.parse_args()
    asyncio.run(main(args.reset, args.port, args.data, args.load, args.demo))
