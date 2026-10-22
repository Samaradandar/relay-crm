# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""File storage: signed upload, permission-checked download. Nothing is public, nothing deleted."""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, PermissionDenied
from app.platform.context import Actor, RequestInfo
from app.platform.files.models import FileRecord
from app.platform.files.storage import PUT_EXPIRES_SECONDS, get_storage
from app.platform.permissions.guard import Effect
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

MAX_SIZE = 25 * 1024 * 1024


@dataclass(frozen=True)
class FileOwnerRule:
    upload_key: str  # permission needed to attach a file
    view_key: str  # permission needed to see/download it (scope-checked on the owner record)


# entity_type -> rules. Each module registers the record types that can carry files.
OWNERS: dict[str, FileOwnerRule] = {}


def register_file_owner(entity_type: str, upload_key: str, view_key: str) -> None:
    OWNERS[entity_type] = FileOwnerRule(upload_key, view_key)


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .") or "file"
    return cleaned[:150]


async def create_upload(
    actor: Actor,
    request: RequestInfo,
    *,
    entity_type: str,
    entity_id: str,
    filename: str,
    content_type: str,
    size: int,
) -> tuple[FileRecord, str]:
    rule = OWNERS.get(entity_type)
    if rule is None:
        raise NotFound("Unknown record type")
    try:  # one spelling per record, so history is not split (BUG-P1-16)
        entity_id = str(uuid.UUID(entity_id.strip()))
    except ValueError:
        pass
    if size <= 0 or size > MAX_SIZE:
        raise Conflict(f"Files must be between 1 byte and {MAX_SIZE // (1024 * 1024)} MB")
    async with UnitOfWork(actor, request) as uow:
        decision = await uow.authorize(rule.upload_key, entity=(entity_type, entity_id))
        if decision.effect in (Effect.REQUEST_ONLY, Effect.NEEDS_APPROVAL):
            raise PermissionDenied("This needs a request to an Admin", {"key": rule.upload_key})
        name = _safe_name(filename)
        key = f"{actor.org_id}/{entity_type}/{entity_id}/{uuid.uuid4().hex}/{name}"
        rec = FileRecord(
            org_id=actor.org_id,
            owner_entity_type=entity_type,
            owner_entity_id=entity_id,
            key=key,
            filename=name,
            content_type=content_type,
            size=size,
            uploaded_by=actor.id,
        )
        uow.session.add(rec)
        await uow.session.flush()
        await uow.audit(
            "file.upload",
            entity_type,
            entity_id,
            new={"file": name, "size": size, "file_id": str(rec.id)},
        )
        url = get_storage().presign_put(key, content_type)
        return rec, url


async def complete_upload(actor: Actor, request: RequestInfo, file_id: uuid.UUID) -> FileRecord:
    async with UnitOfWork(actor, request) as uow:
        rec = await uow.session.get(FileRecord, file_id)
        if rec is None or rec.org_id != actor.org_id or rec.uploaded_by != actor.id:
            raise NotFound("File not found")
        if rec.status == "uploaded":
            return rec
        actual = await get_storage().size_of(rec.key)
        if actual is None:
            raise Conflict("The file has not been uploaded yet")
        if actual > MAX_SIZE:
            raise Conflict("File is too large")
        rec.status, rec.size = "uploaded", actual
        await uow.audit(
            "file.complete",
            rec.owner_entity_type,
            rec.owner_entity_id,
            new={"file_id": str(rec.id)},
        )
        return rec


async def download_url(session: AsyncSession, actor: Actor, file_id: uuid.UUID) -> str:
    rec = await session.get(FileRecord, file_id)
    if (
        rec is None
        or rec.org_id != actor.org_id
        or rec.deleted_at is not None
        or rec.status != "uploaded"
    ):
        raise NotFound("File not found")
    rule = OWNERS.get(rec.owner_entity_type)
    if rule is None:
        raise NotFound("File not found")
    try:  # a hidden file answers exactly like a missing one (BUG-P1-15)
        await authorize_read(
            session, actor, rule.view_key, (rec.owner_entity_type, rec.owner_entity_id)
        )
    except NotFound as exc:
        raise NotFound("File not found") from exc
    return get_storage().presign_get(rec.key, rec.filename)


def upload_expiry_seconds() -> int:
    return PUT_EXPIRES_SECONDS
