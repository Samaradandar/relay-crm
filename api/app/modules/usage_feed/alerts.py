# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Usage-sync-failing alert (P4-09): 3 consecutive failed runs notifies Admins and Super admins,
once per day, deduplicated like every other Phase 4 alert."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.models import BillingAlert
from app.modules.billing.schedule import ist_today
from app.modules.users.models import User
from app.platform.notifications import service as notifications
from app.platform.permissions.roles import Role

ALERT_TYPE = "usage.sync_failing"
NO_REF = uuid.UUID(int=0)


async def alert_sync_failing(session: AsyncSession, org_id: uuid.UUID, failures: int) -> bool:
    today = ist_today()
    exists = (
        await session.execute(
            select(BillingAlert.id).where(
                BillingAlert.org_id == org_id,
                BillingAlert.customer_id == NO_REF,
                BillingAlert.alert_type == ALERT_TYPE,
                BillingAlert.ref_id == NO_REF,
                BillingAlert.alert_date == today,
            )
        )
    ).first()
    if exists is not None:
        return False
    session.add(
        BillingAlert(
            org_id=org_id,
            customer_id=NO_REF,
            alert_type=ALERT_TYPE,
            ref_id=NO_REF,
            alert_date=today,
        )
    )
    admins = list(
        (
            await session.execute(
                select(User.id).where(
                    User.org_id == org_id,
                    User.role.in_((Role.ADMIN, Role.SUPER_ADMIN)),
                    User.is_active.is_(True),
                )
            )
        ).scalars()
    )
    await notifications.notify(
        session,
        org_id,
        admins,
        ALERT_TYPE,
        "Usage sync is failing",
        f"The usage feed pull has failed {failures} times in a row.",
        "/admin/usage",
        dedupe_key=f"{ALERT_TYPE}:{today.isoformat()}",
    )
    return True
