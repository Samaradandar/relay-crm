# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P7-02: CSV upload wizard. Rows are parsed and stashed in csv_import_rows at upload time (not
re-read from S3 on every step -- this environment runs without MinIO outside Docker, and the
`Storage` protocol here has no read-back method yet, see service.py's module docstring); the
original file is still written to S3 best-effort for the audit trail/report download.

Required-or-default fields: company, contact name, phone or email. Everything else in
section 5's mapped-field list is optional.
"""

import csv
import io
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ValidationFailed
from app.core.time import utc_now
from app.modules.accounts.duplicates import find_matches, split
from app.modules.capture import service as capture_service
from app.modules.capture.models import CsvImport, CsvImportRow
from app.modules.capture.service import system_actor
from app.platform.context import Actor, RequestInfo
from app.platform.files.storage import get_storage
from app.platform.uow import UnitOfWork

MAX_ROWS = 5_000
MAX_BYTES = 5 * 1024 * 1024
REQUIRED_OR_DEFAULTED = ("company", "contact_name", "phone", "email")
MAPPABLE_FIELDS = (
    "company", "contact_name", "role", "phone", "email", "website", "industry", "city",
    "lead_source", "next_step", "due_date", "first_contact_date", "notes",
)  # fmt: skip

UPLOAD_KEY = "capture.csv.upload"


def parse_csv_text(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    rows: list[dict[str, str]] = [dict(r) for r in reader]
    if len(rows) > MAX_ROWS:
        raise ValidationFailed(f"CSV has more than {MAX_ROWS} rows", {"field": "file"})
    return headers, rows


async def upload(
    actor: Actor,
    request: RequestInfo,
    filename: str,
    content: bytes,
    default_team_id: uuid.UUID | None,
) -> dict[str, Any]:
    if len(content) > MAX_BYTES:
        raise ValidationFailed("File is larger than 5 MB", {"field": "file"})
    text = content.decode("utf-8-sig", errors="replace")
    headers, rows = parse_csv_text(text)
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(UPLOAD_KEY)
        s3_key = f"csv-imports/{actor.org_id}/{uuid.uuid4()}.csv"
        try:
            await get_storage().put_bytes(s3_key, content, "text/csv")
        except Exception:  # noqa: BLE001 - best-effort audit copy; rows are already captured below
            pass
        imp = CsvImport(
            org_id=actor.org_id,
            file_s3_key=s3_key,
            filename=filename,
            default_team_id=default_team_id,
            status="uploaded",
            created_by=actor.id,
            counts={"total": len(rows)},
            created_at=utc_now(),
        )
        s.add(imp)
        await s.flush()
        for i, row in enumerate(rows, start=1):
            s.add(CsvImportRow(org_id=actor.org_id, import_id=imp.id, row_number=i, raw=row))
        await s.flush()
        await uow.audit(
            UPLOAD_KEY, "csv_import", imp.id, new={"filename": filename, "rows": len(rows)}
        )
        return {"id": imp.id, "headers": headers, "row_count": len(rows)}


def _mapped(row: dict[str, str], mapping: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field, column in mapping.items():
        out[field] = row.get(column)
    return out


async def set_mapping(
    session: AsyncSession,
    actor: Actor,
    import_id: uuid.UUID,
    mapping: dict[str, str],
    default_lead_source: str | None,
) -> dict[str, Any]:
    imp = await session.get(CsvImport, import_id)
    if imp is None or imp.org_id != actor.org_id:
        raise NotFound("Import not found")
    unknown = set(mapping) - set(MAPPABLE_FIELDS)
    if unknown:
        raise ValidationFailed(
            f"Unknown field(s): {', '.join(sorted(unknown))}", {"field": "mapping"}
        )
    imp.mapping = (
        {**mapping, "_default_lead_source": default_lead_source} if default_lead_source else mapping
    )
    await session.flush()
    return await preview(session, actor, import_id)


async def preview(session: AsyncSession, actor: Actor, import_id: uuid.UUID) -> dict[str, Any]:
    """Classifies every row -- ready / duplicate / needs_review / existing_customer / invalid --
    without writing a single deal (PRD: "nothing written yet"). Duplicates WITHIN the file are
    caught by tracking normalised keys seen earlier in this same pass."""
    imp = await session.get(CsvImport, import_id)
    if imp is None or imp.org_id != actor.org_id:
        raise NotFound("Import not found")
    mapping = {k: v for k, v in imp.mapping.items() if not k.startswith("_")}
    default_source = imp.mapping.get("_default_lead_source")
    rows = (
        await session.execute(
            select(CsvImportRow)
            .where(CsvImportRow.import_id == import_id)
            .order_by(CsvImportRow.row_number)
        )
    ).scalars().all()  # fmt: skip

    seen_in_file: set[str] = set()
    counts: dict[str, int] = {}
    for row in rows:
        mapped = _mapped(row.raw, mapping)
        n = capture_service.NormalisedLead(mapped)
        errors: list[str] = []
        if not n.company:
            errors.append("company is required")
        if not (n.phone or n.email):
            errors.append("phone or email is required")
        if mapped.get("phone") and not n.phone:
            errors.append("phone is not valid")
        if mapped.get("email") and not n.email:
            errors.append("email is not valid")
        if errors:
            row.outcome, row.errors = "invalid", errors
        else:
            key = capture_service._idempotency_hash(mapped)  # noqa: SLF001 - same-module family
            if key in seen_in_file:
                row.outcome = "duplicate"
            else:
                seen_in_file.add(key)
                matches = await find_matches(
                    session, system_actor(actor.org_id, "CSV preview"),
                    company=n.company, domain=n.domain, phone_e164=n.phone, email=n.email,
                )  # fmt: skip
                existing = next((m for m in matches if m.is_existing_customer), None)
                block, warn = split(matches)
                if existing:
                    row.outcome = "existing_customer"
                elif block:
                    row.outcome = "duplicate"
                elif warn:
                    row.outcome = "needs_review"
                else:
                    row.outcome = "ready"
        counts[row.outcome or "invalid"] = counts.get(row.outcome or "invalid", 0) + 1
    imp.status, imp.counts = (
        "previewed",
        {**imp.counts, **counts, "default_lead_source": default_source},
    )
    await session.flush()
    return {"id": imp.id, "status": imp.status, "counts": imp.counts}


async def run_import(
    actor: Actor, request: RequestInfo, import_id: uuid.UUID, reason: str | None
) -> dict[str, Any]:
    """Runs in batches of 200, each row through capture_lead(); already-done rows stay done if
    stopped partway (outcome is set in the same transaction as the row's own processing)."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        imp = await s.get(CsvImport, import_id)
        if imp is None or imp.org_id != actor.org_id:
            raise NotFound("Import not found")
        await uow.authorize(UPLOAD_KEY)
        cleaned = uow.reason(UPLOAD_KEY, reason)
        imp.status, imp.reason = "running", cleaned
        await s.flush()
        await uow.audit(UPLOAD_KEY, "csv_import", imp.id, new={"status": "running"}, reason=cleaned)

    mapping_raw = imp.mapping
    mapping = {k: v for k, v in mapping_raw.items() if not k.startswith("_")}
    default_source = mapping_raw.get("_default_lead_source")
    done = created = 0
    # rows are read forward by row number: a row skipped (invalid) or left without a deal (a
    # duplicate) is never read again, so a file with bad rows still finishes
    last = 0
    while True:
        async with UnitOfWork(actor, request) as uow:
            s = uow.session
            imp = await s.get(CsvImport, import_id)
            if imp is None or imp.status == "stopped":
                break
            batch = (
                await s.execute(
                    select(CsvImportRow)
                    .where(
                        CsvImportRow.import_id == import_id,
                        CsvImportRow.deal_id.is_(None),
                        CsvImportRow.row_number > last,
                    )
                    .order_by(CsvImportRow.row_number)
                    .limit(200)
                )
            ).scalars().all()  # fmt: skip
            if not batch:
                imp.status, imp.finished_at = "done", utc_now()
                await s.flush()
                break
            last = batch[-1].row_number
            for row in batch:
                # the preview already decided these: an invalid row, or a duplicate (of an
                # existing lead, or of an earlier row in this same file) is never created
                if row.outcome in ("invalid", "duplicate"):
                    continue
                mapped = _mapped(row.raw, mapping)
                if default_source and not mapped.get("lead_source"):
                    mapped["lead_source"] = default_source
                event = await capture_service.capture_lead(
                    s, actor.org_id, "csv", mapped, uploader=actor,
                    default_team_id=imp.default_team_id, origin="import",
                )  # fmt: skip
                row.deal_id, row.outcome = event.deal_id, event.outcome
                done += 1
                created += 1 if event.outcome == "created" else 0
            await s.flush()
    return {"id": import_id, "status": "done", "processed": done, "created": created}


async def stop(actor: Actor, request: RequestInfo, import_id: uuid.UUID) -> None:
    async with UnitOfWork(actor, request) as uow:
        imp = await uow.session.get(CsvImport, import_id)
        if imp is None or imp.org_id != actor.org_id:
            raise NotFound("Import not found")
        await uow.authorize(UPLOAD_KEY)
        imp.status = "stopped"
        await uow.session.flush()


async def report_rows(
    session: AsyncSession, actor: Actor, import_id: uuid.UUID
) -> list[CsvImportRow]:
    imp = await session.get(CsvImport, import_id)
    if imp is None or imp.org_id != actor.org_id:
        raise NotFound("Import not found")
    return list(
        (
            await session.execute(
                select(CsvImportRow)
                .where(CsvImportRow.import_id == import_id)
                .order_by(CsvImportRow.row_number)
            )
        ).scalars()
    )


_ = date  # kept for the due_date type hint in docstrings above; no runtime use yet
