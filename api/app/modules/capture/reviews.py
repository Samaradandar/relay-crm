# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P7's Capture review queue and settings (website forms, email source). Built in: Phase 7."""

import secrets
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, PermissionDenied, ValidationFailed
from app.core.time import utc_now
from app.modules.capture import events
from app.modules.capture.models import CaptureEvent, CaptureReview, CaptureSource
from app.modules.capture.service import capture_lead
from app.platform.context import Actor, RequestInfo
from app.platform.permissions.roles import Scope
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

SETTINGS_KEY = "capture.settings.manage"
REVIEW_VIEW_KEY = "capture.review.view"
REVIEW_DECIDE_KEY = "capture.review.decide"


# --------------------------------------------------------------------------------- settings
async def list_sources(session: AsyncSession, actor: Actor) -> list[CaptureSource]:
    await authorize_read(session, actor, SETTINGS_KEY)
    return list(
        (
            await session.execute(select(CaptureSource).where(CaptureSource.org_id == actor.org_id))
        ).scalars()
    )


async def create_source(
    actor: Actor,
    request: RequestInfo,
    kind: str,
    name: str,
    allowed_origins: list[str],
    mapping: dict[str, Any],
    default_next_step: str | None,
    default_lead_source: str | None,
    reason: str | None,
) -> CaptureSource:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(SETTINGS_KEY)
        cleaned = uow.reason(SETTINGS_KEY, reason)
        source = CaptureSource(
            org_id=actor.org_id, kind=kind, name=name,
            source_key=secrets.token_urlsafe(24) if kind == "website" else None,
            allowed_origins=allowed_origins, mapping=mapping,
            default_next_step=default_next_step, default_lead_source=default_lead_source,
            active=True, created_by=actor.id, created_at=utc_now(), updated_at=utc_now(),
        )  # fmt: skip
        s.add(source)
        await s.flush()
        await uow.audit(
            SETTINGS_KEY,
            "capture_source",
            source.id,
            new={"name": name, "kind": kind},
            reason=cleaned,
        )
        return source


async def rotate_key(
    actor: Actor, request: RequestInfo, source_id: uuid.UUID, reason: str | None
) -> str:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        source = await s.get(CaptureSource, source_id)
        if source is None or source.org_id != actor.org_id:
            raise NotFound("Source not found")
        await uow.authorize(SETTINGS_KEY)
        cleaned = uow.reason(SETTINGS_KEY, reason)
        old_key, source.source_key = source.source_key, secrets.token_urlsafe(24)
        source.updated_at = utc_now()
        await s.flush()
        await uow.audit(
            SETTINGS_KEY,
            "capture_source",
            source.id,
            old={"key": old_key},
            new={"key": "rotated"},
            reason=cleaned,
        )
        assert source.source_key is not None
        return source.source_key


async def test_source(
    session: AsyncSession, actor: Actor, source_id: uuid.UUID, payload: dict[str, Any]
) -> CaptureEvent:
    await authorize_read(session, actor, SETTINGS_KEY)
    source = await session.get(CaptureSource, source_id)
    if source is None or source.org_id != actor.org_id:
        raise NotFound("Source not found")
    event = CaptureEvent(
        org_id=actor.org_id, source_id=source.id, kind=source.kind, idempotency_key=None,
        payload_preview=payload, outcome="test", created_at=utc_now(),
    )  # fmt: skip
    session.add(event)
    await session.flush()
    return event


# -------------------------------------------------------------------------------- review queue
async def list_reviews(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    decision = await authorize_read(session, actor, REVIEW_VIEW_KEY)
    conds = [CaptureReview.org_id == actor.org_id, CaptureReview.status == "open"]
    if decision.scope == Scope.TEAM:
        conds.append(CaptureReview.routed_team_id == actor.team_id)
    elif decision.scope != Scope.ALL:
        return []
    rows = (
        await session.execute(
            select(CaptureReview, CaptureEvent)
            .join(CaptureEvent, CaptureEvent.id == CaptureReview.capture_event_id)
            .where(*conds)
            .order_by(CaptureReview.created_at)
        )
    ).all()
    return [
        {
            "id": r.id,
            "capture_event_id": e.id,
            "company": e.payload_preview.get("company"),
            "source_kind": e.kind,
            "payload_preview": e.payload_preview,
            "matched_deal_id": r.matched_deal_id,
            "routed_team_id": r.routed_team_id,
            "created_at": r.created_at,
        }  # fmt: skip
        for r, e in rows
    ]


async def decide_review(
    actor: Actor, request: RequestInfo, review_id: uuid.UUID, action: str, reason: str | None
) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        review = await s.get(CaptureReview, review_id)
        if review is None or review.org_id != actor.org_id or review.status != "open":
            raise NotFound("Review not found")
        if (
            review.routed_team_id is not None
            and actor.team_id != review.routed_team_id
            and not actor.is_privileged
        ):
            raise PermissionDenied("This capture is routed to another team")
        await uow.authorize(REVIEW_DECIDE_KEY)
        cleaned = uow.reason(REVIEW_DECIDE_KEY, reason)
        event = await s.get(CaptureEvent, review.capture_event_id)
        if event is None:
            raise NotFound("Capture event not found")

        if action == "discard":
            review.status, review.decided_by, review.decided_at, review.reason = (
                "discarded", actor.id, utc_now(), cleaned,
            )  # fmt: skip
            event.outcome = "rejected_spam"
        elif action == "add_to_existing":
            if review.matched_deal_id is None:
                raise ValidationFailed("No matched deal to add this to", {"field": "action"})
            from app.modules.leads.models import Deal

            deal = await s.get(Deal, review.matched_deal_id)
            if deal is not None:
                flags = dict(deal.flags or {})
                company = event.payload_preview.get("company")
                flags.setdefault("capture_notes", []).append(
                    f"Added from review: {company} ({utc_now().isoformat()})"
                )
                deal.flags = flags
            review.status, review.decided_by, review.decided_at, review.reason = (
                "merged", actor.id, utc_now(), cleaned,
            )  # fmt: skip
            event.outcome, event.deal_id = "duplicate", review.matched_deal_id
        elif action == "create_anyway":
            new_event = await capture_lead(
                s, actor.org_id, event.kind, event.payload_preview,  # type: ignore[arg-type]
                idempotency_key=f"review:{review.id}", origin="integration", force_create=True,
            )  # fmt: skip
            review.status, review.decided_by, review.decided_at, review.reason = (
                "created", actor.id, utc_now(), cleaned,
            )  # fmt: skip
            event.outcome, event.deal_id = new_event.outcome, new_event.deal_id
        else:
            raise ValidationFailed("Unknown action", {"field": "action"})

        await s.flush()
        await uow.audit(
            REVIEW_DECIDE_KEY, "capture_review", review.id, new={"action": action}, reason=cleaned
        )
        await uow.emit(
            events.CAPTURE_REVIEW_DECIDED, {"review_id": str(review.id), "action": action}
        )
        return {"id": review.id, "status": review.status}
