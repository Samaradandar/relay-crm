# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P7-03: ties email_parse.py's rules to capture_lead() and records the inbound_emails row.
Reachable via the manual-entry endpoint (router.py) until a live inbox exists."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.modules.capture import email_parse
from app.modules.capture.models import InboundEmail
from app.modules.capture.service import capture_lead
from app.platform.context import Actor

BLOCKLIST: frozenset[str] = frozenset()  # Settings-managed in a later PR; empty is safe today


async def process_inbound_email(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    message_id: str,
    from_email: str,
    from_name: str | None,
    subject: str | None,
    body: str | None,
    is_auto_reply: bool = False,
    is_bounce: bool = False,
    spam_verdict: str = "PASS",
    forwarding_rep: Actor | None = None,
) -> InboundEmail:
    existing = (
        await session.execute(
            select(InboundEmail).where(
                InboundEmail.org_id == org_id, InboundEmail.message_id == message_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing  # idempotent on Message-ID -- delivered twice, processed once

    ignore_reason = email_parse.is_ignorable(
        is_auto_reply=is_auto_reply, is_bounce=is_bounce, spam_verdict=spam_verdict,
        from_blocklist=from_email.lower() in BLOCKLIST,
    )  # fmt: skip
    deal_id: uuid.UUID | None = None
    if ignore_reason is not None:
        outcome = "ignored"
    else:
        parsed = email_parse.parse_email(
            from_email=from_email, from_name=from_name, subject=subject, body=body
        )
        event = await capture_lead(
            session, org_id, "email", parsed,
            idempotency_key=message_id,
            uploader=forwarding_rep,  # Deal.generated_by: the forwarding rep, else None (system)
            origin="integration",
        )  # fmt: skip
        outcome, deal_id = event.outcome, event.deal_id

    row = InboundEmail(
        org_id=org_id, message_id=message_id, from_email=from_email, from_name=from_name,
        subject=subject, spam_verdict=spam_verdict, outcome=outcome, deal_id=deal_id,
        forwarded_by_user_id=forwarding_rep.id if forwarding_rep else None,
        created_at=utc_now(),
    )  # fmt: skip
    session.add(row)
    await session.flush()
    return row
