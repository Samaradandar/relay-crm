# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Re-applies the permission keys and matrix from code to the database (upsert).

Use after a PRD change:  python -m app.platform.permissions.sync
The first install is seeded by migration 0001; this keeps later changes in data, not in code paths.
"""

import asyncio

from sqlalchemy.dialects.postgresql import insert

from app.core.db import dispose_engine, transaction
from app.platform.permissions import loader
from app.platform.permissions.keys import KEYS
from app.platform.permissions.matrix import seed_rows
from app.platform.permissions.models import PermissionKeyRow, RolePermissionRow


async def sync() -> tuple[int, int]:
    async with transaction() as s:
        for k in KEYS.values():
            stmt = insert(PermissionKeyRow).values(
                key=k.key, action_class=k.action_class.value, description=k.description
            )
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["key"],
                    set_={
                        "action_class": stmt.excluded.action_class,
                        "description": stmt.excluded.description,
                    },
                )  # fmt: skip
            )
        rows = seed_rows()
        for role, key, rule in rows:
            stmt = insert(RolePermissionRow).values(
                role=role.value, key=key, scope=rule.scope.value, mode=rule.mode.value,
                own_deals_only=rule.own_deals_only, not_own_deals=rule.not_own_deals,
                routed_to=rule.routed_to,
            )  # fmt: skip
            await s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["role", "key"],
                    set_={
                        c: getattr(stmt.excluded, c)
                        for c in ("scope", "mode", "own_deals_only", "not_own_deals", "routed_to")
                    },
                )  # fmt: skip
            )
    loader.invalidate()
    return len(KEYS), len(rows)


if __name__ == "__main__":

    async def _main() -> None:
        keys, rows = await sync()
        print(f"synced {keys} permission keys, {rows} role rows")
        await dispose_engine()

    asyncio.run(_main())
