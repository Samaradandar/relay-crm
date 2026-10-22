# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Normalise values before they are stored or compared (PRD 11 Feature 1, duplicate rule)."""

import re
from urllib.parse import urlparse

import phonenumbers

DEFAULT_REGION = "IN"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HOST_LABEL = re.compile(r"^[^\W_](?:[^\W_]|-)*$", re.UNICODE)
_OTHER_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d+(?:/|$))")


def normalize_domain(website: str | None) -> str | None:
    """'https://www.Acme.in/about?x=1' -> 'acme.in'. Returns None when it is not a usable web
    address: only http(s) or no scheme, no spaces or login part, a dotted host whose last label is
    letters (so IP addresses are refused)."""
    if not website or not website.strip():
        return None
    raw = website.strip()
    if any(ch.isspace() or ch == "\x00" for ch in raw):
        return None
    if "://" in raw:
        if raw.split("://", 1)[0].lower() not in ("http", "https"):
            return None
    elif _OTHER_SCHEME.match(raw):
        return None  # javascript:..., mailto:... (a bare 'host:8080' is fine)
    try:
        parsed = urlparse(raw if "://" in raw else "//" + raw)
        host = (parsed.hostname or "").lower().strip(".")
        if parsed.username is not None or parsed.password is not None:
            return None
        _ = parsed.port
    except ValueError:
        return None
    host = host.removeprefix("www.")
    labels = host.split(".")
    if len(labels) < 2 or not all(_HOST_LABEL.match(x) for x in labels):
        return None
    if not labels[-1].isalpha():  # also refuses 192.168.1.1
        return None
    return host


def normalize_phone(phone: str | None, region: str = DEFAULT_REGION) -> str | None:
    """To E.164 (default country India). Returns None if it is not a valid number."""
    if not phone or not phone.strip():
        return None
    try:
        num = phonenumbers.parse(phone.strip(), region)
    except phonenumbers.NumberParseException:
        return None
    if num.extension or not phonenumbers.is_valid_number(num):
        return None  # an extension would be silently dropped (BUG-P2-02)
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def normalize_email(email: str | None) -> str | None:
    if not email or not email.strip():
        return None
    value = email.strip().lower()
    if len(value) > 254 or not _EMAIL.match(value):
        return None
    domain = value.rsplit("@", 1)[1]
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return None
    return value


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None
