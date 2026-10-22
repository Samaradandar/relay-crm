# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P7-01/02/03: the one capture path every source (website form, CSV row, email) calls.
`capture_lead()` must never be bypassed -- it is the only writer of capture_events and the only
caller of leads.service.create_lead() for an auto-captured lead.

Live infra not available yet (confirmed with Samar): no real website to point a form at, no
live inbox for email-to-lead. So this module is deliberately scoped to what's testable without
that infra:
  - website capture: the full capture_lead() pipeline + POST /capture/web/{source_key}, with the
    captcha/rate-limit checks structured as pluggable hooks (no-op until real keys exist) rather
    than left out, so wiring them in later is a config change, not new code.
  - email-to-lead: the parser + capture_lead() integration, reachable via an Admin-only "paste an
    email in" endpoint instead of a live SES/SNS pipeline (nothing to connect to yet).
  - CSV upload: the full wizard (upload, map, preview, run, report), since it needs no external
    infra at all.
"""

import re
import uuid
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.normalize import clean_text, normalize_domain, normalize_email, normalize_phone
from app.core.time import utc_now
from app.modules.accounts.duplicates import Match, find_matches, split
from app.modules.accounts.models import Account
from app.modules.capture import events
from app.modules.capture.models import CaptureEvent, CaptureReview, CaptureSource
from app.modules.leads.handlers import (
    _leader,  # noqa: SLF001 - cross-module reuse (see deals/handlers.py)
)
from app.modules.leads.models import Deal, LeadPoolEntry
from app.modules.leads.schemas import LeadIn
from app.modules.leads.service import create_lead, today_ist
from app.platform.context import Actor, RequestInfo
from app.platform.notifications import service as notifications

Outcome = Literal[
    "created", "duplicate", "existing_customer", "needs_review", "rejected_spam", "invalid", "test"
]
CaptureKind = Literal["website", "csv", "email"]


def system_actor(org_id: uuid.UUID, name: str = "Capture") -> Actor:
    """`app.platform.context.system_actor` (role="system") bypasses the permission matrix via
    `Actor.is_system`, but accounts.duplicates.find_matches reads `actor.role_enum` directly
    (not through that bypass) to resolve scope -- `Role("system")` raises. id=None still makes
    `is_system` True everywhere else, so a role of "super_admin" here is exactly as safe and
    additionally makes every direct role_enum/scope lookup resolve to ALL, as the PRD asks for
    ("run as the system actor, so it sees everything and leaks nothing")."""
    return Actor(id=None, org_id=org_id, role="super_admin", team_id=None, name=name)


CAPTURE_WINDOW_MINUTES = (
    10  # web idempotency: same normalised payload within this window = one lead
)


# ---------------------------------------------------------------------------------- normalise
class NormalisedLead:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.company = clean_text(raw.get("company") or "") or ""
        self.contact_name = clean_text(raw.get("name") or raw.get("contact_name") or "") or ""
        self.phone = normalize_phone(raw.get("phone"))
        self.email = normalize_email(raw.get("email"))
        self.website = raw.get("website")
        self.domain = normalize_domain(self.website) if self.website else None
        self.city = clean_text(raw.get("city")) if raw.get("city") else None
        self.message = clean_text(raw.get("message")) if raw.get("message") else None
        self.solution_interest = (
            clean_text(raw.get("solution_interest")) if raw.get("solution_interest") else None
        )
        # CSV-only: a mapped "next step"/"due date"/"first contact date" column, used instead of
        # the source's default when present (PRD P7-02: "the mapped column or 'First contact'").
        self.lead_source = clean_text(raw.get("lead_source")) if raw.get("lead_source") else None
        self.next_step = clean_text(raw.get("next_step")) if raw.get("next_step") else None
        self.due_date = _parse_date(raw.get("due_date"))
        self.first_contact_date = _parse_date(raw.get("first_contact_date"))
        self.raw = raw


def _parse_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _idempotency_hash(raw: dict[str, Any]) -> str:
    import hashlib
    import json

    key_fields = {k: raw.get(k) for k in ("company", "name", "phone", "email", "website")}
    return hashlib.sha256(json.dumps(key_fields, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------------------------- the core path
async def capture_lead(
    session: AsyncSession,
    org_id: uuid.UUID,
    kind: CaptureKind,
    raw_payload: dict[str, Any],
    *,
    source: CaptureSource | None = None,
    idempotency_key: str | None = None,
    uploader: Actor | None = None,
    default_team_id: uuid.UUID | None = None,
    origin: Literal["integration", "import", "system_job"] = "integration",
    force_create: bool = False,
) -> CaptureEvent:
    """The one path for every capture source. `uploader` is set only for CSV rows (credited as
    `Deal.generated_by`); `default_team_id` is the CSV-uploading Team Leader's own team, which
    PRD confirms is where their rows default to (round robin within that team from there) --
    everything else lets the normal assignment engine pick. `force_create` is the review queue's
    "Create anyway": skips the needs_review hold for a similar-name-only match (never for an
    exact match or an existing customer -- those are never force-created)."""
    n = NormalisedLead(raw_payload)
    idem_key = idempotency_key or _idempotency_hash(raw_payload)
    idem_conds = [
        CaptureEvent.org_id == org_id,
        CaptureEvent.kind == kind,
        CaptureEvent.idempotency_key == idem_key,
    ]
    if source is not None:
        idem_conds.append(CaptureEvent.source_id == source.id)
    existing = (await session.execute(select(CaptureEvent).where(*idem_conds))).scalar_one_or_none()
    if existing is not None:
        return existing  # already captured -- idempotent, same submission delivered twice

    actor = system_actor(org_id, "Capture")
    outcome: Outcome
    deal_id: uuid.UUID | None = None
    matched_account_id: uuid.UUID | None = None
    error: str | None = None

    if not n.company or not (n.phone or n.email):
        outcome, error = "invalid", "Missing company and a phone or email"
    else:
        matches = await find_matches(
            session, actor, company=n.company, domain=n.domain, phone_e164=n.phone, email=n.email
        )
        existing_customer = next((m for m in matches if m.is_existing_customer), None)
        if existing_customer is not None:
            outcome = "existing_customer"
            matched_account_id = existing_customer.account_id
            await _handle_existing_customer(session, org_id, existing_customer, n, kind)
        else:
            block, warn = split(matches)
            if block:
                outcome = "duplicate"
                matched_account_id = block[0].account_id
                await _handle_duplicate(session, org_id, block[0], n, kind)
            elif warn and not force_create:
                outcome = "needs_review"
                matched_account_id = warn[0].account_id
            else:
                outcome = "created"
                deal_id = await _create(
                    session,
                    org_id,
                    actor,
                    n,
                    kind,
                    source,
                    uploader,
                    origin,
                    create_anyway=bool(warn and force_create),
                )
                if default_team_id is not None:
                    await _route_to_team(session, deal_id, default_team_id)

    event = CaptureEvent(
        org_id=org_id,
        source_id=source.id if source else None,
        kind=kind,
        idempotency_key=idem_key,
        payload_preview={
            "company": n.company,
            "contact_name": n.contact_name,
            "phone": n.phone,
            "email": n.email,
            "website": n.website,
            "city": n.city,
        },  # fmt: skip
        outcome=outcome,
        deal_id=deal_id,
        matched_account_id=matched_account_id,
        error=error,
        created_at=utc_now(),
    )
    session.add(event)
    await session.flush()

    if outcome == "needs_review":
        await _open_review(session, org_id, event, warn[0] if "warn" in locals() and warn else None)

    return event


async def _create(
    session: AsyncSession,
    org_id: uuid.UUID,
    actor: Actor,
    n: NormalisedLead,
    kind: CaptureKind,
    source: CaptureSource | None,
    uploader: Actor | None,
    origin: str,
    create_anyway: bool = False,
) -> uuid.UUID:
    default_source = (
        n.lead_source
        or (source.default_lead_source if source else None)
        or {"website": "Website", "csv": "Website", "email": "Inbound email"}[kind]
    )
    default_step = (
        n.next_step
        or (source.default_next_step if source else None)
        or {
            "website": "Call the new website lead",
            "csv": "First contact",
            "email": "Reply to the enquiry",
        }[kind]
    )
    if kind == "csv":
        due = n.due_date or _next_working_day(today_ist())
    else:
        due = today_ist()
    body = LeadIn(
        company=n.company,
        website=n.website,
        city=n.city,
        contact_name=n.contact_name or n.company,
        phone=n.phone,
        email=n.email,
        lead_source=default_source,  # type: ignore[arg-type]
        next_step_text=default_step,
        next_step_due_date=due,
        first_contact_date=n.first_contact_date,
        create_anyway=create_anyway,
        override_block=False,
        reason="Created anyway from the capture review queue" if create_anyway else None,
    )
    deal_id, _cap_warning, _skip_engine = await create_lead(actor, RequestInfo(origin=origin), body)
    await session.execute(
        update(Deal)
        .where(Deal.id == deal_id)
        .values(
            source_kind=kind,
            capture_source_id=source.id if source else None,
            generated_by=uploader.id if uploader else None,
        )
    )
    return deal_id


def _next_working_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:  # Sat=5, Sun=6
        nxt += timedelta(days=1)
    return nxt


async def _route_to_team(session: AsyncSession, deal_id: uuid.UUID, team_id: uuid.UUID) -> None:
    """A TL's CSV upload defaults to their own team (confirmed with Samar); round robin then
    runs inside that team like any other pooled lead. Patches the still-open pool entry right
    after creation -- the assignment engine dispatches via the outbox worker, not inline, so this
    lands before it runs for the ordinary case of a background CSV import."""
    await session.execute(
        update(LeadPoolEntry)
        .where(LeadPoolEntry.deal_id == deal_id, LeadPoolEntry.left_at.is_(None))
        .values(routed_team_id=team_id)
    )
    await session.execute(update(Deal).where(Deal.id == deal_id).values(team_id=team_id))


async def _handle_existing_customer(
    session: AsyncSession, org_id: uuid.UUID, match: Match, n: NormalisedLead, kind: CaptureKind
) -> None:
    account = await session.get(Account, match.account_id)
    if account is None:
        return
    notify_ids = [account.account_owner_id] if account.account_owner_id else []
    if not notify_ids:
        team_id = (
            await session.execute(
                select(Deal.team_id).where(Deal.account_id == account.id).limit(1)
            )
        ).scalar_one_or_none()
        leader = await _leader(session, team_id)
        if leader:
            notify_ids = [leader]
    await notifications.notify(
        session, org_id, notify_ids, events.CAPTURE_EXISTING_CUSTOMER,
        f"New enquiry from an existing customer: {account.name}",
        f"Via {kind}: {n.message or n.contact_name}", f"/accounts/{account.id}",
        dedupe_key=f"capture.existing_customer:{account.id}:{n.phone or n.email}",
    )  # fmt: skip


async def _handle_duplicate(
    session: AsyncSession, org_id: uuid.UUID, match: Match, n: NormalisedLead, kind: CaptureKind
) -> None:
    if match.deal_id is None:
        return  # out of a hypothetical caller's scope -- never happens for the system actor
    deal = await session.get(Deal, match.deal_id)
    if deal is None:
        return
    flags = dict(deal.flags or {})
    notes = flags.setdefault("capture_notes", [])
    notes.append(
        f"Came in again via {kind}: {n.message or n.contact_name} ({utc_now().isoformat()})"
    )
    deal.flags = flags
    if deal.owner_id:
        await notifications.notify(
            session, org_id, [deal.owner_id], events.CAPTURE_DUPLICATE,
            "A lead came in again",
            f"{match.company} submitted another enquiry via {kind}.", f"/deals/{deal.id}",
            dedupe_key=f"capture.duplicate:{deal.id}:{utc_now().date().isoformat()}",
        )  # fmt: skip


async def _open_review(
    session: AsyncSession, org_id: uuid.UUID, event: CaptureEvent, match: Match | None
) -> None:
    team_id = None
    if match and match.deal_id:
        team_id = (
            await session.execute(select(Deal.team_id).where(Deal.id == match.deal_id))
        ).scalar_one_or_none()
    review = CaptureReview(
        org_id=org_id, capture_event_id=event.id, matched_deal_id=match.deal_id if match else None,
        routed_team_id=team_id, status="open", created_at=utc_now(),
    )  # fmt: skip
    session.add(review)
    await session.flush()
    leader = await _leader(session, team_id)
    notify_ids = [leader] if leader else []
    await notifications.notify(
        session, org_id, notify_ids, events.CAPTURE_HELD_FOR_REVIEW,
        "A captured lead needs review",
        f"A similar company already exists for {event.payload_preview.get('company')}.",
        "/pool?tab=review",
        dedupe_key=f"capture.review:{event.id}",
    )  # fmt: skip


# -------------------------------------------------------------------------------- spam checks
DISPOSABLE_DOMAINS = frozenset({
    "mailinator.com", "10minutemail.com", "guerrillamail.com", "yopmail.com", "trashmail.com",
    "tempmail.com", "fakeinbox.com", "throwawaymail.com",
})  # fmt: skip


def is_disposable_email(email: str | None) -> bool:
    if not email or "@" not in email:
        return False
    return email.rsplit("@", 1)[1].lower() in DISPOSABLE_DOMAINS


def honeypot_tripped(payload: dict[str, Any]) -> bool:
    """A hidden field real visitors never fill in; a bot usually does."""
    return bool(payload.get("website_url_hp") or payload.get("hp_field"))


async def verify_captcha_token(token: str | None) -> bool:
    """No-op until real Turnstile/reCAPTCHA keys exist (confirmed with Samar): accepts anything
    including no token, so the pipeline is wired and this becomes a config change later, not new
    code. NEVER remove this hook to "simplify" -- it's the only thing standing between a real
    site and the spam list once keys are added."""
    return True


_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def valid_email_shape(email: str | None) -> bool:
    return bool(email and _EMAIL_RE.fullmatch(email))
