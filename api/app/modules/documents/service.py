# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Manual proposal, agreement and invoice records (PRD 11, P3-15). Automation is Phase 4.

A document can be the proof for an exit criterion; editing or deleting one re-checks the ticks that
depend on it ('Proof missing'). Files are uploaded through the normal signed-upload flow against
the deal, then attached here by id.
"""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.time import utc_now
from app.modules.deals.common import ensure_live_deal, load_deal
from app.modules.documents.models import Document
from app.modules.leads.events import DEAL_UPDATED
from app.modules.leads.models import Deal
from app.modules.leads.ownership import company_name, deal_rt, names
from app.modules.pipeline.engine.service import recheck_proofs
from app.platform.approvals import service as approvals
from app.platform.context import Actor, RequestInfo
from app.platform.files.models import FileRecord
from app.platform.permissions.guard import Effect
from app.platform.permissions.matrix import ROUTE_ADMIN_POOL
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

ADD_KEY = {
    "proposal": "deal.edit",
    "agreement": "agreement.add",
    "invoice": "invoice.add",
    "advance": "deal.edit",
    "prerequisite": "deal.edit",
    "technical": "deal.edit",
}
EDIT_KEY = {
    "proposal": "deal.edit",
    "agreement": "agreement.edit",
    "invoice": "invoice.edit",
    "advance": "deal.edit",
    "prerequisite": "deal.edit",
    "technical": "deal.edit",
}
ALLOWED = {
    "proposal": {"draft", "sent"},
    "agreement": {"draft", "sent", "countersigned", "terminated"},
    "invoice": {"draft", "sent"},
    "advance": {"received"},  # documents the company receives
    "prerequisite": {"received"},
    "technical": {"received"},
}
NEEDS_FILE = {
    ("agreement", "sent"),
    ("agreement", "countersigned"),
    ("advance", "received"),
    ("prerequisite", "received"),
    ("technical", "received"),
}
INVOICE_EDIT_APPROVAL = "invoice.edit"


async def _check_file(session: AsyncSession, deal: Deal, file_id: uuid.UUID | None) -> None:
    if file_id is None:
        return
    rec = await session.get(FileRecord, file_id)
    if (
        rec is None
        or rec.org_id != deal.org_id
        or rec.deleted_at is not None
        or rec.status != "uploaded"
        or rec.owner_entity_type != "deal"
        or rec.owner_entity_id != str(deal.id)
    ):
        raise ValidationFailed(
            "Upload the file to this deal first, then attach it", {"field": "file_id"}
        )


def _check_status(
    kind: str, status: str, file_id: uuid.UUID | None, *, terminate: bool = False
) -> None:
    if status not in ALLOWED[kind]:
        raise ValidationFailed(f"A {kind} cannot have status '{status}'", {"field": "status"})
    if status == "terminated" and not terminate:
        raise ValidationFailed("Use the terminate action", {"field": "status"})
    if (kind, status) in NEEDS_FILE and file_id is None:
        raise ValidationFailed(f"A {status} {kind} needs its file", {"field": "file_id"})


async def add_document(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, body: Any
) -> uuid.UUID:
    if body.status == "terminated":
        raise ValidationFailed("A new document cannot start as terminated", {"field": "status"})
    _check_status(body.kind, body.status, body.file_id)
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await load_deal(uow, deal_id, lock=True)
        key = ADD_KEY[body.kind]
        await uow.authorize(key, entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason(key, body.reason)
        if deal.status not in ("open", "won"):
            raise Conflict("Documents are added to open deals")
        await _check_file(s, deal, body.file_id)
        n = (
            await s.execute(
                select(func.coalesce(func.max(Document.version_no), 0)).where(
                    Document.deal_id == deal.id, Document.kind == body.kind
                )
            )
        ).scalar_one()
        doc = Document(
            org_id=deal.org_id,
            deal_id=deal.id,
            kind=body.kind,
            version_no=n + 1,
            status=body.status,
            sent_at=body.sent_at
            or (utc_now() if body.status in ("sent", "countersigned") else None),
            file_id=body.file_id,
            amount_paise=body.amount_paise,
            number=body.number,
            notes=body.notes,
            created_by=actor.id,
        )
        s.add(doc)
        await s.flush()
        await recheck_proofs(uow, deal)
        deal.version, deal.updated_at = deal.version + 1, utc_now()
        await uow.audit(
            key,
            "deal",
            deal.id,
            new={"document": body.kind, "version": doc.version_no, "status": doc.status,
                 "document_id": doc.id.hex},
            reason=cleaned,
        )  # fmt: skip
        await uow.emit(DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))
        return doc.id


async def _apply_patch(
    uow: UnitOfWork, doc: Document, deal: Deal, patch: dict[str, Any]
) -> dict[str, Any]:
    old: dict[str, Any] = {}
    if "status" in patch or "file_id" in patch:
        _check_status(
            doc.kind,
            patch.get("status", doc.status),
            patch["file_id"] if "file_id" in patch else doc.file_id,
            terminate=True,
        )
    if "file_id" in patch:
        await _check_file(uow.session, deal, patch["file_id"])
    if (
        "status" in patch
        and patch["status"] in ("sent", "countersigned")
        and not patch.get("sent_at")
    ):
        patch.setdefault("sent_at", doc.sent_at or utc_now())
    for k, v in patch.items():
        if getattr(doc, k) != v:
            old[k] = str(getattr(doc, k)) if getattr(doc, k) is not None else None
            setattr(doc, k, v)
    doc.updated_at = utc_now()
    await uow.session.flush()
    await recheck_proofs(uow, deal)
    deal.version, deal.updated_at = deal.version + 1, utc_now()
    await uow.emit(DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))
    return old


async def _load(uow: UnitOfWork, doc_id: uuid.UUID) -> tuple[Document, Deal]:
    doc = (
        await uow.session.execute(
            select(Document)
            .where(
                Document.id == doc_id,
                Document.org_id == uow.actor.org_id,
                Document.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if doc is None:
        raise NotFound("Document not found")
    deal = await load_deal(uow, doc.deal_id, lock=True)
    return doc, deal


async def edit_document(
    actor: Actor, request: RequestInfo, doc_id: uuid.UUID, patch: Any
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Returns (document id, approval id when the change became a request)."""
    sent = patch.model_fields_set - {"reason"}
    if not sent:
        raise ValidationFailed("Nothing to change")
    data = {k: getattr(patch, k) for k in sent}
    async with UnitOfWork(actor, request) as uow:
        doc, deal = await _load(uow, doc_id)
        terminating = data.get("status") == "terminated"
        if terminating and doc.kind != "agreement":
            raise ValidationFailed("Only an agreement can be terminated")
        key = "agreement.terminate" if terminating else EDIT_KEY[doc.kind]
        decision = await uow.authorize(key, entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason(key, patch.reason)
        if decision.effect in (Effect.NEEDS_APPROVAL, Effect.REQUEST_ONLY):
            req = await approvals.create_request(
                uow,
                type_=INVOICE_EDIT_APPROVAL,
                action_key=key,
                summary=f"{actor.name} asks to edit {doc.kind} v{doc.version_no} for "
                f"{await company_name(uow.session, deal)}",
                payload={
                    "document_id": str(doc.id),
                    "patch": {k: (str(v) if isinstance(v, uuid.UUID) else v)
                              for k, v in data.items()
                              if not hasattr(v, "isoformat")},
                },
                reason=patch.reason,
                routed_to=(decision.rule.routed_to if decision.rule else None) or ROUTE_ADMIN_POOL,
                team_id=deal.team_id,
                entity=("deal", str(deal.id)),
                record_owner_id=deal.owner_id,
            )  # fmt: skip
            return doc.id, req.id
        old = await _apply_patch(uow, doc, deal, data)
        await uow.audit(
            key,
            "deal",
            deal.id,
            old=old,
            new={k: str(v) for k, v in data.items()},
            reason=cleaned,
            note=f"{doc.kind} v{doc.version_no}",
        )
        return doc.id, None


async def _exec_invoice_edit(uow: UnitOfWork, req: Any) -> None:
    doc, deal = await _load(uow, uuid.UUID(req.payload["document_id"]))
    patch = dict(req.payload["patch"])
    if "file_id" in patch and patch["file_id"]:
        patch["file_id"] = uuid.UUID(patch["file_id"])
    old = await _apply_patch(uow, doc, deal, patch)
    await uow.audit(
        req.action_key, "deal", deal.id, old=old, new=patch, note="Approved document edit"
    )


async def delete_document(
    actor: Actor, request: RequestInfo, doc_id: uuid.UUID, reason: str | None
) -> None:
    async with UnitOfWork(actor, request) as uow:
        doc, deal = await _load(uow, doc_id)
        key = EDIT_KEY[doc.kind]
        decision = await uow.authorize(key, entity=("deal", deal.id), owner_id=deal.owner_id)
        if decision.effect != Effect.ALLOW:
            raise ValidationFailed("Ask an Admin to delete this document")
        cleaned = uow.reason(key, reason)
        doc.deleted_at, doc.deleted_reason = utc_now(), cleaned
        await uow.session.flush()
        await recheck_proofs(uow, deal)
        deal.version, deal.updated_at = deal.version + 1, utc_now()
        await uow.audit(
            key, "deal", deal.id, old={"document": doc.kind, "version": doc.version_no},
            reason=cleaned, note="Document deleted",
        )  # fmt: skip
        await uow.emit(DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))


async def list_for_deal(
    session: AsyncSession, actor: Actor, deal_id: uuid.UUID
) -> list[dict[str, Any]]:
    await authorize_read(session, actor, "deal.view", ("deal", deal_id))
    await ensure_live_deal(session, actor, deal_id)
    rows = list(
        (
            await session.execute(
                select(Document)
                .where(
                    Document.deal_id == deal_id,
                    Document.org_id == actor.org_id,
                    Document.deleted_at.is_(None),
                )
                .order_by(Document.kind, Document.version_no)
            )
        ).scalars()
    )
    return await _out(session, rows)


async def _out(session: AsyncSession, rows: list[Document]) -> list[dict[str, Any]]:
    who = await names(session, {r.created_by for r in rows})
    fids = {r.file_id for r in rows if r.file_id}
    files = (
        {
            f.id: f.filename
            for f in (
                await session.execute(select(FileRecord).where(FileRecord.id.in_(fids)))
            ).scalars()
        }
        if fids
        else {}
    )
    return [
        {
            "id": d.id,
            "deal_id": d.deal_id,
            "kind": d.kind,
            "version_no": d.version_no,
            "status": d.status,
            "sent_at": d.sent_at,
            "file_id": d.file_id,
            "file_name": files.get(d.file_id) if d.file_id else None,
            "amount_paise": d.amount_paise,
            "number": d.number,
            "notes": d.notes,
            "created_by_name": who.get(d.created_by) if d.created_by else None,
            "created_at": d.created_at,
        }
        for d in rows
    ]


async def one(session: AsyncSession, doc_id: uuid.UUID) -> dict[str, Any]:
    d = await session.get(Document, doc_id)
    assert d is not None
    return (await _out(session, [d]))[0]


def register() -> None:
    approvals.register_type(INVOICE_EDIT_APPROVAL, _exec_invoice_edit)
