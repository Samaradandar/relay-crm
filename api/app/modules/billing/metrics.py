# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Pure reporting functions over billing data. No HTTP endpoint yet -- Phase 6 wires these up.

Built in: Phase 5 (P5-03).
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.models import Subscription


async def renewal_rate(
    session: AsyncSession, org_id: UUID, period_start: date, period_end: date
) -> Decimal | None:
    """Renewed / (contracts whose contract_end_date fell in [period_start, period_end]).

    None when no contract ended in the period (the ratio is undefined, not zero)."""
    rows = (
        (
            await session.execute(
                select(Subscription.renewal_status).where(
                    Subscription.org_id == org_id,
                    Subscription.deleted_at.is_(None),
                    Subscription.contract_end_date.is_not(None),
                    Subscription.contract_end_date >= period_start,
                    Subscription.contract_end_date <= period_end,
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    renewed = sum(1 for r in rows if r == "renewed")
    return Decimal(renewed) / Decimal(len(rows))
