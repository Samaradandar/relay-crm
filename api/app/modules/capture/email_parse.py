# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P7-03: email-to-lead parsing. Reachable today via an Admin-only "paste an email in" endpoint
(no live SES/SNS inbox yet, see service.py's module docstring) but the parsing rules here are
the same ones a real inbound message would go through.
"""

import re
from typing import Any

PHONE_RE = re.compile(r"(?:\+?\d[\d\-\s()]{7,}\d)")
WEBSITE_RE = re.compile(
    r"\b(?:https?://)?(?:www\.)?([a-z0-9-]+\.[a-z]{2,}(?:\.[a-z]{2,})?)\b", re.I
)
AUTO_REPLY_HEADERS = ("Auto-Submitted", "X-Autoreply")
FORWARD_SUBJECT_RE = re.compile(r"^\s*lead\s*:", re.I)


def is_ignorable(
    *, is_auto_reply: bool, is_bounce: bool, spam_verdict: str, from_blocklist: bool
) -> str | None:
    """None = process it; otherwise the reason it's ignored (logged, no lead)."""
    if is_auto_reply:
        return "auto_reply"
    if is_bounce:
        return "bounce"
    if spam_verdict.upper() not in ("PASS", "GRAY"):
        return "spam_verdict"
    if from_blocklist:
        return "blocklisted_sender"
    return None


def is_forwarded_lead(subject: str | None) -> bool:
    """A rep forwards a lead with 'Lead:' in the subject so it still creates one, instead of
    being treated as an internal sender (PRD)."""
    return bool(subject and FORWARD_SUBJECT_RE.match(subject))


def parse_signature(body: str | None) -> dict[str, Any]:
    """Phone/company/website from the signature block, simple rules first. Company falls back
    to the sender's email domain by the caller, not here (needs the sender's address)."""
    body = body or ""
    phone = None
    m = PHONE_RE.search(body)
    if m:
        digits = re.sub(r"[^\d+]", "", m.group(0))
        if len(re.sub(r"\D", "", digits)) >= 8:
            phone = digits
    website = None
    for m in WEBSITE_RE.finditer(body):
        host = m.group(1).lower()
        if host not in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com"):
            website = host
            break
    return {"phone": phone, "website": website}


def parse_email(
    *, from_email: str, from_name: str | None, subject: str | None, body: str | None
) -> dict[str, Any]:
    sig = parse_signature(body)
    domain = from_email.split("@", 1)[1] if "@" in from_email else None
    company = sig.get("website") or domain or ""
    return {
        "name": from_name or from_email.split("@", 1)[0],
        "email": from_email,
        "phone": sig.get("phone"),
        "company": company,
        "website": sig.get("website") or domain,
        "message": subject or "",
    }
