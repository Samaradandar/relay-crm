# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for capture: the public website form, settings, CSV upload, the manual
email-entry endpoint (P7-03's stand-in for a live inbox) and the review queue.

Built in: Phase 7. See Relay CRM Build Plan.
"""

import uuid
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.core.errors import NotFound
from app.modules.capture import csv_import, inbound_email, reviews
from app.modules.capture import service as capture_service
from app.modules.capture.models import CaptureSource
from app.modules.capture.schemas import (
    CaptureSourceIn,
    CsvMappingIn,
    CsvRunIn,
    ManualEmailIn,
    ReviewDecideIn,
    WebCaptureIn,
)
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["capture"])


# -------------------------------------------------------------------------------- public form
@router.post("/capture/web/{source_key}", status_code=202)
async def capture_web(source_key: str, body: WebCaptureIn) -> dict[str, bool]:
    """Always 202, whatever the input -- a visitor or a bot never learns the outcome."""
    async with get_sessionmaker()() as s, s.begin():
        source = (
            await s.execute(
                select(CaptureSource).where(
                    CaptureSource.source_key == source_key, CaptureSource.active.is_(True)
                )
            )
        ).scalar_one_or_none()
        if source is None:
            return {"ok": True}  # an unknown/disabled key is indistinguishable from success
        if capture_service.honeypot_tripped(body.model_dump()):
            return {"ok": True}
        if not await capture_service.verify_captcha_token(body.captcha_token):
            return {"ok": True}
        if capture_service.is_disposable_email(body.email):
            return {"ok": True}
        if not body.consent_text:
            return {"ok": True}  # PRD: no consent, no lead
        payload = body.model_dump(exclude={"captcha_token", "website_url_hp", "idempotency_key"})
        await capture_service.capture_lead(
            s, source.org_id, "website", payload, source=source,
            idempotency_key=body.idempotency_key, origin="integration",
        )  # fmt: skip
    return {"ok": True}


# ------------------------------------------------------------------------------------ settings
@router.get("/settings/capture/sources")
async def get_sources(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        sources = await reviews.list_sources(s, actor)
        return [
            {
                "id": src.id,
                "kind": src.kind,
                "name": src.name,
                "source_key": src.source_key,
                "allowed_origins": src.allowed_origins,
                "mapping": src.mapping,
                "default_next_step": src.default_next_step,
                "default_lead_source": src.default_lead_source,
                "active": src.active,
            }  # fmt: skip
            for src in sources
        ]


@router.post("/settings/capture/sources", status_code=201)
async def post_source(body: CaptureSourceIn, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    source = await reviews.create_source(
        actor, req, body.kind, body.name, body.allowed_origins, body.mapping,
        body.default_next_step, body.default_lead_source, body.reason,
    )  # fmt: skip
    return {"id": source.id, "source_key": source.source_key}


@router.post("/settings/capture/sources/{source_id}/rotate-key")
async def rotate(
    source_id: uuid.UUID, actor: ActorDep, req: RequestDep, reason: str | None = None
) -> dict[str, str]:
    key = await reviews.rotate_key(actor, req, source_id, reason)
    return {"source_key": key}


@router.post("/settings/capture/sources/{source_id}/test")
async def test(source_id: uuid.UUID, payload: dict[str, Any], actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s, s.begin():
        event = await reviews.test_source(s, actor, source_id, payload)
        return {"id": event.id, "outcome": event.outcome}


# -------------------------------------------------------------------------- email-to-lead (manual)
@router.post("/internal/inbound-email")
async def manual_inbound_email(body: ManualEmailIn, actor: ActorDep) -> dict[str, Any]:
    """Admin/SA-only stand-in for the live SES/SNS webhook (no inbox yet)."""
    if not actor.is_privileged:
        from app.core.errors import PermissionDenied

        raise PermissionDenied("Admin or Super admin only")
    async with get_sessionmaker()() as s, s.begin():
        row = await inbound_email.process_inbound_email(
            s, actor.org_id, message_id=body.message_id, from_email=body.from_email,
            from_name=body.from_name, subject=body.subject, body=body.body,
            is_auto_reply=body.is_auto_reply, is_bounce=body.is_bounce,
            spam_verdict=body.spam_verdict,
        )  # fmt: skip
        return {"id": row.id, "outcome": row.outcome, "deal_id": row.deal_id}


# --------------------------------------------------------------------------------- review queue
@router.get("/capture/reviews")
async def get_reviews(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await reviews.list_reviews(s, actor)


@router.post("/capture/reviews/{review_id}/decide")
async def decide(
    review_id: uuid.UUID, body: ReviewDecideIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await reviews.decide_review(actor, req, review_id, body.action, body.reason)


@router.get("/capture/events")
async def get_events(
    actor: ActorDep, source: uuid.UUID | None = None, outcome: str | None = None
) -> list[dict[str, Any]]:
    from app.modules.capture.models import CaptureEvent
    from app.platform.reads import authorize_read

    async with get_sessionmaker()() as s:
        await authorize_read(s, actor, "capture.events.view")
        conds = [CaptureEvent.org_id == actor.org_id]
        if source:
            conds.append(CaptureEvent.source_id == source)
        if outcome:
            conds.append(CaptureEvent.outcome == outcome)
        stmt = (
            select(CaptureEvent).where(*conds).order_by(CaptureEvent.created_at.desc()).limit(500)
        )
        rows = (await s.execute(stmt)).scalars().all()
        return [
            {
                "id": e.id,
                "source_id": e.source_id,
                "kind": e.kind,
                "outcome": e.outcome,
                "deal_id": e.deal_id,
                "error": e.error,
                "created_at": e.created_at,
            }  # fmt: skip
            for e in rows
        ]


# --------------------------------------------------------------------------------- CSV upload
@router.post("/imports", status_code=201)
async def upload_csv(
    actor: ActorDep,
    req: RequestDep,
    file: UploadFile = File(...),  # noqa: B008 - FastAPI's own pattern for file uploads
    default_team_id: uuid.UUID | None = Form(None),  # noqa: B008
) -> dict[str, Any]:
    content = await file.read()
    team_id = default_team_id or (actor.team_id if actor.role == "team_leader" else None)
    return await csv_import.upload(actor, req, file.filename or "upload.csv", content, team_id)


@router.put("/imports/{import_id}/mapping")
async def put_mapping(import_id: uuid.UUID, body: CsvMappingIn, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s, s.begin():
        return await csv_import.set_mapping(
            s, actor, import_id, body.mapping, body.default_lead_source
        )


@router.post("/imports/{import_id}/run")
async def run(
    import_id: uuid.UUID, body: CsvRunIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await csv_import.run_import(actor, req, import_id, body.reason)


@router.post("/imports/{import_id}/stop")
async def stop_import(import_id: uuid.UUID, actor: ActorDep, req: RequestDep) -> dict[str, bool]:
    await csv_import.stop(actor, req, import_id)
    return {"ok": True}


@router.get("/imports/{import_id}")
async def get_import(import_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        from app.modules.capture.models import CsvImport

        imp = await s.get(CsvImport, import_id)
        if imp is None or imp.org_id != actor.org_id:
            raise NotFound("Import not found")
        return {"id": imp.id, "status": imp.status, "counts": imp.counts, "filename": imp.filename}


@router.get("/imports/{import_id}/report.csv")
async def report_csv(import_id: uuid.UUID, actor: ActorDep) -> PlainTextResponse:
    import csv
    import io

    async with get_sessionmaker()() as s:
        rows = await csv_import.report_rows(s, actor, import_id)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["row_number", "outcome", "deal_id", "errors"])
        for r in rows:
            w.writerow([r.row_number, r.outcome, r.deal_id, "; ".join(r.errors)])
        return PlainTextResponse(out.getvalue(), media_type="text/csv")
