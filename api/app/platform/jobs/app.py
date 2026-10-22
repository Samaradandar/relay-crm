# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Procrastinate (a job queue inside Postgres) and the schedules (all times IST in comments).

Schedules (cron is evaluated in UTC; IST = UTC + 5:30):
  outbox dispatcher   every minute (safety net; the worker also drains it continuously)
  approval reminder   daily 09:30 IST  = 04:00 UTC
  audit chain check   nightly 00:30 IST = 19:00 UTC
"""

import logging
import uuid

import procrastinate
from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.time import utc_now
from app.modules.users.models import Org, User
from app.platform.approvals import service as approvals
from app.platform.audit import service as audit_service
from app.platform.notifications import service as notifications
from app.platform.outbox.service import dispatch_batch
from app.platform.permissions.roles import Role

log = logging.getLogger("relay.jobs")


def conninfo() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


def make_app() -> procrastinate.App:
    return procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=conninfo()))


app = make_app()

OUTBOX_CRON = "* * * * *"
REMINDER_CRON = "0 4 * * *"
AUDIT_CHECK_CRON = "0 19 * * *"


@app.periodic(cron=OUTBOX_CRON)
@app.task(name="outbox_dispatch", queueing_lock="outbox_dispatch")
async def outbox_dispatch(timestamp: int) -> int:
    return await dispatch_batch(get_sessionmaker())


@app.periodic(cron=REMINDER_CRON)
@app.task(name="approval_reminders", queueing_lock="approval_reminders")
async def approval_reminders(timestamp: int) -> int:
    return await approvals.send_pending_reminders(get_sessionmaker())


HEALTH_CRON = "0 19 * * *"  # 00:30 IST
POOL_RETRY_CRON = "*/15 * * * *"


@app.periodic(cron=POOL_RETRY_CRON)
@app.task(name="pool_retry", queueing_lock="pool_retry")
async def pool_retry(timestamp: int) -> int:
    """Every 15 minutes: hand rep-created leads whose Team Leader hold window is over to automatic
    round robin (so a hold lasts at most the window plus 15 minutes), then try waiting Lead Pool
    entries again (a rep may have freed capacity)."""
    from app.modules.leads.assignment.service import expire_rep_holds, retry_waiting

    async with get_sessionmaker()() as s:
        org_ids = list((await s.execute(select(Org.id))).scalars())
    total = 0
    for org_id in org_ids:
        total += await expire_rep_holds(org_id)
        total += await retry_waiting(org_id, get_sessionmaker())
    return total


@app.periodic(cron=HEALTH_CRON)
@app.task(name="health_refresh", queueing_lock="health_refresh")
async def health_refresh(timestamp: int) -> int:
    """Daily 00:30 IST: recalculate Nudge / Stale / At risk / Slipped and queue the digests."""
    from app.modules.deals.today import refresh_health

    return (await refresh_health(get_sessionmaker()))["changed"]


FOLLOWUP_DUE_CRON = "30 3 * * *"  # 09:00 IST


@app.periodic(cron=FOLLOWUP_DUE_CRON)
@app.task(name="followup_due_daily", queueing_lock="followup_due_daily")
async def followup_due_daily(timestamp: int) -> int:
    """Daily 09:00 IST: one digest per owner of next steps due today (PRD 11 'Follow-up due',
    P5-08)."""
    from app.modules.deals.today import send_followup_digest

    return await send_followup_digest(get_sessionmaker())


async def check_audit_chains() -> dict[uuid.UUID, bool]:
    """Re-compute every org's audit chain; alert the Super admins if one is broken."""
    results: dict[uuid.UUID, bool] = {}
    async with get_sessionmaker()() as s:
        org_ids = list((await s.execute(select(Org.id))).scalars())
    for org_id in org_ids:
        async with get_sessionmaker()() as s, s.begin():
            res = await audit_service.verify_chain(s, org_id)
            results[org_id] = res.ok
            if not res.ok:
                log.error(
                    "AUDIT CHAIN BROKEN org=%s seq=%s: %s", org_id, res.first_bad_seq, res.problem
                )
                sas = list(
                    (
                        await s.execute(
                            select(User.id).where(
                                User.org_id == org_id,
                                User.role == Role.SUPER_ADMIN,
                                User.is_active.is_(True),
                            )
                        )
                    ).scalars()
                )
                await notifications.notify(
                    s, org_id, sas, "audit.chain_broken", "Audit trail integrity problem",
                    f"The audit log failed its nightly check at row {res.first_bad_seq}: "
                    f"{res.problem}",
                    "/admin", dedupe_key=f"audit.chain_broken:{res.first_bad_seq}",
                )  # fmt: skip
    return results


@app.periodic(cron=AUDIT_CHECK_CRON)
@app.task(name="audit_chain_check", queueing_lock="audit_chain_check")
async def audit_chain_check(timestamp: int) -> int:
    results = await check_audit_chains()
    return sum(1 for ok in results.values() if not ok)


BILLING_ALERTS_CRON = "0 4 * * *"  # 09:30 IST


@app.periodic(cron=BILLING_ALERTS_CRON)
@app.task(name="billing_alerts_daily", queueing_lock="billing_alerts_daily")
async def billing_alerts_daily(timestamp: int) -> int:
    """Daily 09:30 IST: low balance, run-out, overage, payment due/overdue, awaiting
    confirmation (PRD P4-09/P4-10)."""
    from app.modules.billing.alerts import run_all

    async with get_sessionmaker()() as s:
        org_ids = list((await s.execute(select(Org.id))).scalars())
    total = 0
    for org_id in org_ids:
        async with get_sessionmaker()() as s, s.begin():
            result = await run_all(s, org_id)
            total += sum(result.values())
    return total


PHASE5_BILLING_ALERTS_CRON = "0 4 * * *"  # 09:30 IST


@app.periodic(cron=PHASE5_BILLING_ALERTS_CRON)
@app.task(name="phase5_billing_alerts_daily", queueing_lock="phase5_billing_alerts_daily")
async def phase5_billing_alerts_daily(timestamp: int) -> int:
    """Daily 09:30 IST: contract approaching end, payment-overdue-and-minutes-out churn risk,
    contract ended without renewal (PRD P5-04). The cancellation-requested churn-risk alert is
    event-driven (see billing/alerts_p5.py's outbox handler), not part of this polling job."""
    from app.modules.billing.alerts_p5 import run_all_p5

    async with get_sessionmaker()() as s:
        org_ids = list((await s.execute(select(Org.id))).scalars())
    total = 0
    for org_id in org_ids:
        async with get_sessionmaker()() as s, s.begin():
            result = await run_all_p5(s, org_id)
            total += sum(result.values())
    return total


PROPOSALS_PENDING_CRON = "0 4 * * *"  # 09:30 IST


@app.periodic(cron=PROPOSALS_PENDING_CRON)
@app.task(name="proposals_pending_daily", queueing_lock="proposals_pending_daily")
async def proposals_pending_daily(timestamp: int) -> int:
    """Daily 09:30 IST: a proposal sent 2+ days ago with no feedback notifies its deal owner once
    (PRD 11 'Proposal pending', P5-08)."""
    from app.modules.proposals.service import check_pending_no_feedback

    async with get_sessionmaker()() as s:
        org_ids = list((await s.execute(select(Org.id))).scalars())
    total = 0
    for org_id in org_ids:
        async with get_sessionmaker()() as s, s.begin():
            total += await check_pending_no_feedback(s, org_id)
    return total


PERFORMANCE_NIGHTLY_CRON = "30 19 * * *"  # 01:00 IST


@app.periodic(cron=PERFORMANCE_NIGHTLY_CRON)
@app.task(name="performance_nightly", queueing_lock="performance_nightly")
async def performance_nightly(timestamp: int) -> int:
    """Daily 01:00 IST: recompute the current month for every active sales rep from source
    tables (P6-01), logging a warning on any drift from the live-updated values. Also checks
    whether a new IST month just started and, if so, freezes last month and opens this one
    (P6-01's "month close") instead of recomputing the now-previous month."""
    from app.core.time import to_ist
    from app.modules.performance.stats import (
        close_month_if_due,
        current_ist_month,
        recompute_org_month,
    )

    now = utc_now()
    async with get_sessionmaker()() as s:
        org_ids = list((await s.execute(select(Org.id))).scalars())
    total = 0
    for org_id in org_ids:
        async with get_sessionmaker()() as s, s.begin():
            if await close_month_if_due(s, org_id, to_ist(now).date()):
                total += 1
            else:
                total += await recompute_org_month(s, org_id, current_ist_month(now))
    return total


GOOGLE_CHANNEL_RENEW_CRON = "0 */6 * * *"  # every 6 hours


@app.periodic(cron=GOOGLE_CHANNEL_RENEW_CRON)
@app.task(name="google_channel_renew", queueing_lock="google_channel_renew")
async def google_channel_renew(timestamp: int) -> int:
    """Every 6 hours: renew any connected rep's calendar push channel expiring within 24h
    (ST-03). A no-op per org when nothing is close to expiring."""
    from app.core.config import get_settings
    from app.modules.google.incremental_sync import renew_expiring_channels

    base_url = (
        get_settings().google_redirect_uri.rsplit("/settings", 1)[0] or "http://localhost:8000"
    )
    async with get_sessionmaker()() as s:
        org_ids = list((await s.execute(select(Org.id))).scalars())
    total = 0
    for org_id in org_ids:
        async with get_sessionmaker()() as s, s.begin():
            total += await renew_expiring_channels(s, org_id, base_url)
    return total


USAGE_SYNC_CRON = "0 * * * *"  # hourly
USAGE_SYNC_FAILURE_ALERT = 3


@app.periodic(cron=USAGE_SYNC_CRON)
@app.task(name="usage_sync", queueing_lock="usage_sync")
async def usage_sync(timestamp: int) -> int:
    """Pull usage for every org (PRD P4-08). After 3 consecutive failed runs, alert Admins and
    Super admins once (deduplicated like every other alert, by date)."""
    from app.modules.usage_feed.alerts import alert_sync_failing
    from app.modules.usage_feed.client import fetch_page
    from app.modules.usage_feed.models import UsageSyncState
    from app.modules.usage_feed.service import run_sync

    async with get_sessionmaker()() as s:
        org_ids = list((await s.execute(select(Org.id))).scalars())
    pulled = 0
    for org_id in org_ids:
        try:
            result = await run_sync(org_id, get_sessionmaker(), fetch_page)
            pulled += result["pulled"]
        except Exception:  # noqa: BLE001 - run_sync already recorded the failure; alert, continue
            log.exception("usage sync failed for org %s", org_id)
        async with get_sessionmaker()() as s2:
            state = await s2.get(UsageSyncState, org_id)
            if state is not None and state.consecutive_failures >= USAGE_SYNC_FAILURE_ALERT:
                async with get_sessionmaker()() as s3, s3.begin():
                    await alert_sync_failing(s3, org_id, state.consecutive_failures)
    return pulled
