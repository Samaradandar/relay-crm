# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Load the real organisation from a JSON file: teams, people, and lead-assignment settings.

    python scripts/load_org.py scripts/org_config.json

Safe to run twice (existing teams and users are skipped; settings are replaced as a whole).
Everything goes through the real services, so it is audited like any other change.
See org_config.example.json for the format. Copy it to org_config.json (git-ignored) and fill it in.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import dispose_engine, get_sessionmaker
from app.modules.leads.assignment.service import write_settings
from app.modules.leads.schemas import AssignmentSettingsIn, RuleIn
from app.modules.users import service
from app.modules.users.models import Team, User
from app.platform import registry
from app.platform.context import RequestInfo, system_actor
from app.platform.permissions.sync import sync

REASON = "organisation load"


async def load(cfg: dict[str, Any]) -> None:
    registry.setup()
    await sync()
    actor = system_actor(get_settings().org_id, "Org loader")
    req = RequestInfo(origin="system_job")

    async with get_sessionmaker()() as s:
        teams = {
            t.name: t.id
            for t in (await s.execute(select(Team).where(Team.deleted_at.is_(None)))).scalars()
        }
    for name in cfg["teams"]:
        if name not in teams:
            teams[name] = (await service.create_team(actor, req, name, REASON)).id

    for p in cfg["people"]:
        email = p["email"].strip().lower()
        async with get_sessionmaker()() as s:
            exists = (
                await s.execute(select(User.id).where(func.lower(User.email) == email))
            ).first()
        if exists:
            continue
        team = p.get("team")
        if team is not None and team not in teams:
            raise SystemExit(f"{email}: unknown team {team!r}")
        await service.create_user(
            actor, req, name=p["name"], email=email, role=p["role"],
            team_id=teams[team] if team else None, reason=REASON,
        )  # fmt: skip

    a = cfg.get("assignment")
    if a:
        async with get_sessionmaker()() as s:
            users = {u.email.lower(): u.id for u in (await s.execute(select(User))).scalars()}
        if a["default_team"] not in teams:
            raise SystemExit(f"assignment.default_team {a['default_team']!r} is not a team")
        rules = []
        for i, r in enumerate(a.get("rules", [])):
            if r["target_type"] == "team":
                target = teams[r["target"]]
            elif r["target_type"] == "user":
                target = users[r["target"].lower()]
            else:
                raise SystemExit("rules: target_type must be 'team' or 'user' (groups: use the UI)")
            rules.append(
                RuleIn(name=r["name"], priority=r.get("priority", (i + 1) * 10), active=True,
                       match=r.get("match", {}), target_type=r["target_type"], target_id=target)
            )  # fmt: skip
        await write_settings(
            actor, req,
            AssignmentSettingsIn(
                default_team_id=teams[a["default_team"]],
                default_cap=a["default_cap"],
                rules=rules,
                caps={users[e.lower()]: n for e, n in a.get("caps", {}).items()},
                reason=REASON,
            ),
        )  # fmt: skip
    print(f"loaded: {len(cfg['teams'])} teams, {len(cfg['people'])} people")
    sender = cfg.get("email_from")
    if sender:
        print(f"Set EMAIL_FROM={sender} in the environment (and verify it in SES for production).")


async def main(cfg: dict[str, Any]) -> None:
    await load(cfg)
    await dispose_engine()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/load_org.py <org_config.json>")
    asyncio.run(main(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))))
