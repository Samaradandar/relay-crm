# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Local sample data: one org (made by the migration), two teams, a Super admin, an Admin,
two Team Leaders (one per team) and four Users (two per team). Safe to run twice.

Goes through the real services, so every row has audit history like production data would.
"""

import asyncio

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.modules.catalog.models import Product
from app.modules.catalog.schemas import ProductIn
from app.modules.catalog.service import put_products
from app.modules.leads.assignment.service import settings_row, write_settings
from app.modules.leads.schemas import AssignmentSettingsIn
from app.modules.users import service
from app.modules.users.models import Team, User
from app.platform import registry
from app.platform.context import RequestInfo, system_actor
from app.platform.permissions.sync import sync

TEAMS = ["Team North", "Team South"]
PEOPLE = [
    ("Samar Gupta", "samar@relay.local", "super_admin", None),
    ("Anita Rao", "anita@relay.local", "admin", None),
    ("Tara Nair", "tara@relay.local", "team_leader", "Team North"),
    ("Tejas Shah", "tejas@relay.local", "team_leader", "Team South"),
    ("Priya Menon", "priya@relay.local", "user", "Team North"),
    ("Rahul Verma", "rahul@relay.local", "user", "Team North"),
    ("Sneha Iyer", "sneha@relay.local", "user", "Team South"),
    ("Vikram Singh", "vikram@relay.local", "user", "Team South"),
]


async def seed() -> None:
    registry.setup()
    await sync()
    actor = system_actor(get_settings().org_id, "Seed script")
    req = RequestInfo(origin="system_job")
    async with get_sessionmaker()() as s:
        team_ids = {
            t.name: t.id
            for t in (await s.execute(select(Team).where(Team.deleted_at.is_(None)))).scalars()
        }
    for name in TEAMS:
        if name not in team_ids:
            team = await service.create_team(actor, req, name, "dev seed")
            team_ids[name] = team.id
    for name, email, role, team in PEOPLE:
        async with get_sessionmaker()() as s:
            exists = (
                await s.execute(select(User.id).where(func.lower(User.email) == email))
            ).first()
        if exists:
            continue
        await service.create_user(
            actor, req, name=name, email=email, role=role,
            team_id=team_ids[team] if team else None, reason="dev seed",
        )  # fmt: skip
    async with get_sessionmaker()() as s:
        current = await settings_row(s, actor.org_id)
        needs_default = current.default_team_id is None
    if needs_default:  # new leads go to Team North until an Admin sets rules
        await write_settings(
            actor,
            req,
            AssignmentSettingsIn(
                default_team_id=team_ids["Team North"], default_cap=25, reason="dev seed"
            ),
        )
    async with get_sessionmaker()() as s:
        has_products = (await s.execute(select(Product.id).limit(1))).first() is not None
    if not has_products:  # sample catalog (prices are placeholders; Admin edits them)
        await put_products(
            actor,
            req,
            [
                ProductIn(name="Standard plan (per unit)", unit="per_minute",
                          list_price_paise=600, floor_price_paise=450),
                ProductIn(name="Onboarding package", unit="fixed",
                          list_price_paise=2500000, floor_price_paise=2000000),
            ],
            "dev seed",
        )  # fmt: skip
    print(
        f"seeded: {len(TEAMS)} teams, {len(PEOPLE)} users. Sign in (dev login) as any @relay.local"
    )


async def main() -> None:
    await seed()
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
