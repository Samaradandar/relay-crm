# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Append-only audit trail with a per-org hash chain (PRD 12).

row_hash = sha256(prev_hash + canonical JSON of the row). Rows are inserted one at a time per
org under an advisory lock so the chain cannot fork. A nightly job re-computes the chain.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.platform.audit.models import AuditLog
from app.platform.context import Actor, RequestInfo

GENESIS = "GENESIS"

_HASHED_FIELDS = (
    "id",
    "org_id",
    "occurred_at",
    "actor_id",
    "actor_role",
    "on_behalf_of",
    "action_key",
    "entity_type",
    "entity_id",
    "outcome",
    "old_value",
    "new_value",
    "reason",
    "approval_id",
    "request_id",
    "ip",
    "user_agent",
    "origin",
    "note",
)


def _canon(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def compute_hash(prev_hash: str, row: AuditLog) -> str:
    body = {f: _canon(getattr(row, f)) for f in _HASHED_FIELDS}
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256((prev_hash + blob).encode("utf-8")).hexdigest()


async def append(
    session: AsyncSession,
    actor: Actor,
    action_key: str,
    entity_type: str,
    entity_id: str | uuid.UUID,
    *,
    outcome: str = "ok",
    old: dict[str, Any] | None = None,
    new: dict[str, Any] | None = None,
    reason: str | None = None,
    note: str | None = None,
    request: RequestInfo | None = None,
    on_behalf_of: uuid.UUID | None = None,
    approval_id: uuid.UUID | None = None,
) -> AuditLog:
    """Write one audit row inside the caller's transaction."""
    req = request or RequestInfo()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"audit:{actor.org_id}"},
    )
    prev = (
        await session.execute(
            select(AuditLog.row_hash)
            .where(AuditLog.org_id == actor.org_id)
            .order_by(AuditLog.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    row = AuditLog(
        id=uuid.uuid4(),
        org_id=actor.org_id,
        occurred_at=utc_now(),
        actor_id=actor.id,
        actor_role=actor.role,
        on_behalf_of=on_behalf_of,
        action_key=action_key,
        entity_type=entity_type,
        entity_id=str(entity_id),
        outcome=outcome,
        old_value=old,
        new_value=new,
        reason=reason,
        approval_id=approval_id,
        request_id=req.request_id,
        ip=req.ip,
        user_agent=req.user_agent,
        origin=req.origin,
        note=note,
        prev_hash=prev or GENESIS,
    )
    row.row_hash = compute_hash(row.prev_hash, row)
    session.add(row)
    await session.flush()
    # Insert-only copy of the head outside the app role's reach to change (BUG-P1-10).
    await session.execute(
        text("INSERT INTO audit_anchor (org_id, seq, row_hash) VALUES (:o, :s, :h)"),
        {"o": row.org_id, "s": row.seq, "h": row.row_hash},
    )
    return row


@dataclass(frozen=True)
class ChainResult:
    ok: bool
    checked: int
    first_bad_seq: int | None = None
    problem: str | None = None


async def verify_chain(session: AsyncSession, org_id: uuid.UUID) -> ChainResult:
    rows = (
        await session.execute(
            select(AuditLog).where(AuditLog.org_id == org_id).order_by(AuditLog.seq)
        )
    ).scalars()
    prev = GENESIS
    n = 0
    for row in rows:
        n += 1
        if row.prev_hash != prev:
            return ChainResult(False, n, row.seq, "broken link (row removed or reordered)")
        if compute_hash(row.prev_hash, row) != row.row_hash:
            return ChainResult(False, n, row.seq, "row content does not match its hash")
        prev = row.row_hash
    # Compare with the insert-only anchors: a deleted newest row, a wiped trail or a re-computed
    # chain no longer matches what was recorded when each row was written.
    bad = (
        await session.execute(
            text(
                "SELECT a.seq, (l.seq IS NULL) AS missing FROM audit_anchor a "
                "LEFT JOIN audit_log l ON l.org_id = a.org_id AND l.seq = a.seq "
                "WHERE a.org_id = :o AND (l.seq IS NULL OR l.row_hash <> a.row_hash) "
                "ORDER BY a.seq LIMIT 1"
            ),
            {"o": org_id},
        )
    ).first()
    if bad is not None:
        why = (
            "audit row removed (anchor has no matching row)"
            if bad.missing
            else "row hash differs from the anchor recorded when it was written"
        )
        return ChainResult(False, n, bad.seq, why)
    return ChainResult(True, n)


async def history(
    session: AsyncSession,
    org_id: uuid.UUID,
    entity_type: str,
    entity_id: str,
    *,
    limit: int = 50,
    before_seq: int | None = None,
) -> list[AuditLog]:
    stmt = select(AuditLog).where(
        AuditLog.org_id == org_id,
        AuditLog.entity_type == entity_type,
        AuditLog.entity_id == entity_id,
    )
    if before_seq is not None:
        stmt = stmt.where(AuditLog.seq < before_seq)
    stmt = stmt.order_by(AuditLog.seq.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars())
