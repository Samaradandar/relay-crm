# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""CSV exports (PRD 4.4 I, P3-17): scoped to what the person may see, a reason on every export,
every export logged with its filters and row count.

Small exports are produced inside the request; larger ones are queued on the outbox and finished
by a worker, then fetched with a short-lived signed link. Scope is applied again when the file is
built, using the requester's role at that moment.
"""

import csv
import io
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.db import get_sessionmaker
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.money import format_rupees
from app.core.time import format_ist, utc_now
from app.modules.accounts.models import Account
from app.modules.activities.models import Activity
from app.modules.churn.models import ChurnEvent
from app.modules.customers.models import Customer
from app.modules.exports.models import ExportLog
from app.modules.leads.models import Deal
from app.modules.leads.ownership import names, team_names
from app.modules.pipeline.config.models import Stage
from app.modules.users.models import User
from app.platform.context import Actor, RequestInfo
from app.platform.files.models import FileRecord
from app.platform.files.storage import get_storage
from app.platform.outbox.service import on_event
from app.platform.permissions import loader
from app.platform.permissions.roles import Scope
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

EXPORT_EVENT = "export.requested"
INLINE_LIMIT = 500
ENTITIES = ("deals", "activities", "churn_history")
FILTER_KEYS = (
    "status",
    "stage_key",
    "owner_id",
    "team_id",
    "lead_source",
    "health",
    "date_from",
    "date_to",
    "reason",
)


def _safe(value: Any) -> str:
    """Stop spreadsheet formula injection: a cell that starts like a formula is quoted."""
    if value is None:
        return ""
    s = value.isoformat() if isinstance(value, datetime) else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _clean_filters(raw: dict[str, Any]) -> dict[str, str]:
    bad = set(raw) - set(FILTER_KEYS)
    if bad:
        raise ValidationFailed(f"Unknown filter: {', '.join(sorted(bad))}")
    out = {k: str(v) for k, v in raw.items() if v not in (None, "")}
    for key in ("owner_id", "team_id"):
        if key in out:
            try:
                out[key] = str(uuid.UUID(out[key]))
            except ValueError:
                raise ValidationFailed(f"{key} must be an id", {"field": key}) from None
    return out


async def _actor_for(session: AsyncSession, log: ExportLog) -> Actor:
    u = await session.get(User, log.user_id)
    if u is None or not u.is_active:
        raise Conflict("The person who asked for this export is no longer active")
    return Actor(
        id=u.id, org_id=u.org_id, role=u.role, team_id=u.team_id, name=u.name, email=u.email
    )


async def _scope_for(session: AsyncSession, actor: Actor) -> Scope:
    return (await loader.get_table(session))[(actor.role_enum, "export.create")].scope


def _deal_where(actor: Actor, scope: Scope, f: dict[str, str]) -> list[Any]:
    where: list[Any] = [
        *scope_conditions(actor, Deal, scope, team_attr="team_id"),
        Deal.deleted_at.is_(None),
    ]
    if "status" in f:
        where.append(Deal.status == f["status"])
    if "stage_key" in f:
        # a stage page can cover several stages (Revision and Revision 2): match its whole group
        s1, s2 = aliased(Stage), aliased(Stage)
        group = (
            select(s1.display_group)
            .where(s1.version_id == Deal.pipeline_version_id, s1.key == f["stage_key"])
            .correlate(Deal)
            .scalar_subquery()
        )
        where.append(
            or_(
                Deal.stage_key == f["stage_key"],
                Deal.stage_key.in_(
                    select(s2.key)
                    .where(s2.version_id == Deal.pipeline_version_id, s2.display_group == group)
                    .correlate(Deal)
                ),
            )
        )
    if "owner_id" in f:
        where.append(Deal.owner_id == uuid.UUID(f["owner_id"]))
    if "team_id" in f:
        where.append(Deal.team_id == uuid.UUID(f["team_id"]))
    if "lead_source" in f:
        where.append(Deal.lead_source == f["lead_source"])
    if "health" in f:
        where.append(Deal.health == f["health"])
    return where


async def _churn_history_where(session: AsyncSession, actor: Actor, f: dict[str, str]) -> list[Any]:
    """Scoped like `customer.view` (own/team/all via the churned customer's account_owner_id),
    not `export.create`'s own scope -- Churn History's scope comes from churn/history.py, so this
    reuses that scoping, not `_deal_where`/`_scope_for` (which are Deal-shaped)."""
    from app.modules.churn.history import customer_scope_where, history_filters

    where, _scope = await customer_scope_where(session, actor)
    where = list(where)
    date_from = date.fromisoformat(f["date_from"]) if "date_from" in f else None
    date_to = date.fromisoformat(f["date_to"]) if "date_to" in f else None
    owner_id = uuid.UUID(f["owner_id"]) if "owner_id" in f else None
    where += history_filters(
        date_from=date_from, date_to=date_to, owner_id=owner_id, reason=f.get("reason")
    )
    return where


async def _rows(
    session: AsyncSession, actor: Actor, entity: str, f: dict[str, str]
) -> tuple[list[str], list[list[Any]]]:
    if entity == "churn_history":
        where = await _churn_history_where(session, actor, f)
        events = list(
            (
                await session.execute(
                    select(ChurnEvent)
                    .join(Customer, Customer.id == ChurnEvent.customer_id)
                    .where(*where)
                    .order_by(ChurnEvent.churn_date)
                )
            ).scalars()
        )
        account_id_by_customer = (
            {
                c.id: c.account_id
                for c in (
                    await session.execute(
                        select(Customer).where(Customer.id.in_({e.customer_id for e in events}))
                    )
                ).scalars()
            }
            if events
            else {}
        )
        account_names = (
            {
                a.id: a.name
                for a in (
                    await session.execute(
                        select(Account).where(Account.id.in_(set(account_id_by_customer.values())))
                    )
                ).scalars()
            }
            if account_id_by_customer
            else {}
        )

        def _company(e: "ChurnEvent") -> str | None:
            acc_id = account_id_by_customer.get(e.customer_id)
            return account_names.get(acc_id) if acc_id else None

        who = await names(session, {e.recorded_by for e in events})
        header = [
            "Churn event id",
            "Company",
            "Churn date",
            "Reason",
            "Notes",
            "Monthly value lost (INR)",
            "Subscription start",
            "Time as customer (days)",
            "Last renewal",
            "Recorded by",
            "Returned",
        ]
        return header, [
            [
                e.id,
                _company(e),
                e.churn_date,
                e.reason,
                e.notes,
                format_rupees(e.monthly_value_lost_paise),
                e.subscription_start,
                e.time_as_customer_days,
                e.last_renewal_date,
                who.get(e.recorded_by),
                e.returned_deal_id is not None and e.reversed_at is None,
            ]
            for e in events
        ]
    scope = await _scope_for(session, actor)
    where = _deal_where(actor, scope, f)
    if entity == "deals":
        deals = list(
            (await session.execute(select(Deal).where(*where).order_by(Deal.created_at))).scalars()
        )
        accounts = (
            {
                a.id: a.name
                for a in (
                    await session.execute(
                        select(Account).where(Account.id.in_({d.account_id for d in deals}))
                    )
                ).scalars()
            }
            if deals
            else {}
        )
        who = await names(session, {d.owner_id for d in deals})
        teams = await team_names(session, {d.team_id for d in deals})
        header = [
            "Deal id",
            "Company",
            "Lead source",
            "Status",
            "Stage",
            "Owner",
            "Team",
            "Value (INR)",
            "Expected close",
            "Health",
            "Created",
            "Assigned",
            "Last activity",
        ]
        data = [
            [
                d.id,
                accounts.get(d.account_id),
                d.lead_source,
                d.status,
                d.stage_key,
                who.get(d.owner_id) if d.owner_id else "",
                teams.get(d.team_id) if d.team_id else "",
                format_rupees(d.value_paise),
                d.expected_close_date,
                d.health,
                format_ist(d.created_at),
                format_ist(d.assigned_at) if d.assigned_at else "",
                format_ist(d.last_activity_at) if d.last_activity_at else "",
            ]
            for d in deals
        ]
        return header, data
    if entity == "activities":
        acts = list(
            (
                await session.execute(
                    select(Activity, Account.name)
                    .join(Deal, Deal.id == Activity.deal_id)
                    .join(Account, Account.id == Activity.account_id)
                    .where(*where, Activity.deleted_at.is_(None))
                    .order_by(Activity.occurred_at)
                )
            ).all()
        )
        who = await names(session, {a.attributed_to for a, _ in acts})
        header = ["Activity id", "Company", "Type", "When", "Two-way", "By", "Discussed"]
        return header, [
            [
                a.id,
                c,
                a.type,
                format_ist(a.occurred_at),
                a.two_way,
                who.get(a.attributed_to),
                a.discussed,
            ]
            for a, c in acts
        ]
    raise ValidationFailed("Unknown export", {"field": "entity"})


async def _count(session: AsyncSession, actor: Actor, entity: str, f: dict[str, str]) -> int:
    if entity == "churn_history":
        where = await _churn_history_where(session, actor, f)
        q = (
            select(func.count())
            .select_from(ChurnEvent)
            .join(Customer, Customer.id == ChurnEvent.customer_id)
            .where(*where)
        )
        return (await session.execute(q)).scalar_one()
    scope = await _scope_for(session, actor)
    where = _deal_where(actor, scope, f)
    if entity == "deals":
        q = select(func.count()).select_from(Deal).where(*where)
    else:
        q = (
            select(func.count())
            .select_from(Activity)
            .join(Deal, Deal.id == Activity.deal_id)
            .where(*where, Activity.deleted_at.is_(None))
        )
    return (await session.execute(q)).scalar_one()


async def request_export(
    actor: Actor, request: RequestInfo, *, entity: str, filters: dict[str, Any], reason: str | None
) -> uuid.UUID:
    if entity not in ENTITIES:
        raise ValidationFailed("Unknown export", {"field": "entity"})
    f = _clean_filters(filters)
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize("export.create")
        cleaned = uow.reason("export.create", reason)
        rows = await _count(s, actor, entity, f)
        log = ExportLog(
            org_id=actor.org_id,
            user_id=actor.id,
            entity=entity,
            filters=f,
            reason=cleaned or "",
            row_count=rows,
        )
        s.add(log)
        await s.flush()
        await uow.audit(
            "export.create",
            "export",
            log.id,
            new={"entity": entity, "filters": f, "rows": rows},
            reason=cleaned,
        )
        if rows > INLINE_LIMIT:
            await uow.emit(EXPORT_EVENT, {"export_id": str(log.id)})
        export_id = log.id
    if rows <= INLINE_LIMIT:
        await run_export(export_id)
    return export_id


async def run_export(export_id: uuid.UUID) -> None:
    """Build the CSV and store it. Safe to run again (an export that is done is left alone)."""
    async with get_sessionmaker()() as s:
        log = await s.get(ExportLog, export_id)
        if log is None:
            raise NotFound("Export not found")
        if log.status == "done":
            return
        actor = await _actor_for(s, log)
        header, data = await _rows(s, actor, log.entity, dict(log.filters))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        for row in data:
            w.writerow([_safe(c) for c in row])
        key = f"{log.org_id}/exports/{log.id}/{log.entity}-{utc_now():%Y%m%d-%H%M}.csv"
        await get_storage().put_bytes(key, buf.getvalue().encode("utf-8-sig"), "text/csv")
    async with get_sessionmaker()() as s2, s2.begin():
        log2 = await s2.get(ExportLog, export_id)
        assert log2 is not None
        rec = FileRecord(
            org_id=log2.org_id,
            owner_entity_type="export",
            owner_entity_id=str(log2.id),
            key=key,
            filename=key.rsplit("/", 1)[-1],
            content_type="text/csv",
            size=len(buf.getvalue()),
            status="uploaded",
            uploaded_by=log2.user_id,
        )
        s2.add(rec)
        await s2.flush()
        log2.file_id, log2.status, log2.row_count = rec.id, "done", len(data)


@on_event(EXPORT_EVENT)
async def _export_handler(_: AsyncSession, event: Any) -> None:
    await run_export(uuid.UUID(event.payload["export_id"]))


async def _can_see(session: AsyncSession, actor: Actor, log: ExportLog) -> bool:
    if log.user_id == actor.id:
        return True
    table = await loader.get_table(session)
    rule = table.get((actor.role_enum, "export_log.view"))
    if rule is None or rule.denied:
        return False
    if rule.scope == Scope.ALL:
        return True
    owner = await session.get(User, log.user_id)
    return bool(owner and owner.team_id == actor.team_id and rule.scope == Scope.TEAM)


async def _log_out(session: AsyncSession, rows: list[ExportLog]) -> list[dict[str, Any]]:
    who = await names(session, {r.user_id for r in rows})
    return [
        {
            "id": r.id,
            "entity": r.entity,
            "filters": r.filters,
            "row_count": r.row_count,
            "reason": r.reason,
            "status": r.status,
            "created_at": r.created_at,
            "user_name": who.get(r.user_id),
            "user_id": r.user_id,
        }
        for r in rows
    ]


async def get_export(session: AsyncSession, actor: Actor, export_id: uuid.UUID) -> dict[str, Any]:
    log = await session.get(ExportLog, export_id)
    if log is None or log.org_id != actor.org_id or not await _can_see(session, actor, log):
        raise NotFound("Export not found")
    out = (await _log_out(session, [log]))[0]
    if log.status == "done" and log.file_id and log.user_id == actor.id:
        rec = await session.get(FileRecord, log.file_id)
        if rec is not None:
            out["download_url"] = get_storage().presign_get(rec.key, rec.filename)
    return out


async def list_log(session: AsyncSession, actor: Actor, limit: int = 100) -> list[dict[str, Any]]:
    """The export log: Team Leaders see their team's, Admin and Super admin see all."""
    decision = await authorize_read(session, actor, "export_log.view")
    q = select(ExportLog).where(ExportLog.org_id == actor.org_id)
    if decision.scope == Scope.TEAM:
        q = q.where(
            ExportLog.user_id.in_(
                select(User.id).where(User.org_id == actor.org_id, User.team_id == actor.team_id)
            )
        )
    rows = list(
        (await session.execute(q.order_by(ExportLog.created_at.desc()).limit(limit))).scalars()
    )
    return await _log_out(session, rows)


async def list_mine(session: AsyncSession, actor: Actor, limit: int = 50) -> list[dict[str, Any]]:
    rows = list(
        (
            await session.execute(
                select(ExportLog)
                .where(ExportLog.org_id == actor.org_id, ExportLog.user_id == actor.id)
                .order_by(ExportLog.created_at.desc())
                .limit(limit)
            )
        ).scalars()
    )
    return await _log_out(session, rows)
