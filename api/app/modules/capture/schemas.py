# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Pydantic request shapes for capture (website form, CSV upload, email-to-lead, review queue).

Built in: Phase 7.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.fields import Opt2000, Raw2000, Text500


class WebCaptureIn(BaseModel):
    """The public website-form body. No `extra='forbid'` -- a real form may post stray UTM/hidden
    fields we don't need; silently dropping them is correct here, unlike every authenticated
    write in this app. Every field is optional -- even company/name -- because the spec requires
    a 202 for every submission including invalid ones (so bots learn nothing); capture_lead()
    is what decides outcome="invalid", not Pydantic validation."""

    company: Opt2000 = None
    name: Opt2000 = None
    phone: Raw2000 = None
    email: Raw2000 = None
    website: Raw2000 = None
    city: Opt2000 = None
    message: Opt2000 = None
    solution_interest: Opt2000 = None
    utm_source: Opt2000 = None
    utm_medium: Opt2000 = None
    utm_campaign: Opt2000 = None
    page_url: Opt2000 = None
    consent_text: Opt2000 = None
    consent_at: datetime | None = None
    captcha_token: Opt2000 = None
    idempotency_key: Opt2000 = None
    # honeypot: a real visitor never sees or fills this field (hidden via CSS on the form).
    website_url_hp: Opt2000 = None


class CaptureSourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern="^(website|email|csv_template)$")
    name: Text500
    allowed_origins: list[str] = []
    mapping: dict[str, Any] = {}
    default_next_step: Opt2000 = None
    default_lead_source: Opt2000 = None
    active: bool = True
    reason: Opt2000 = None


class CaptureSourceOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    source_key: str | None
    allowed_origins: list[str]
    mapping: dict[str, Any]
    default_next_step: str | None
    default_lead_source: str | None
    active: bool


class ReviewDecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(create_anyway|add_to_existing|discard)$")
    reason: Opt2000 = None


class ManualEmailIn(BaseModel):
    """Stand-in for the live SES/SNS pipeline (not available yet, see service.py's module
    docstring): an Admin or Super admin pastes an email's envelope in and it runs through the
    exact same parser and capture_lead() path a real inbound message would."""

    model_config = ConfigDict(extra="forbid")

    message_id: Text500
    from_email: Text500
    from_name: Opt2000 = None
    subject: Opt2000 = None
    body: Opt2000 = None
    is_auto_reply: bool = False
    is_bounce: bool = False
    spam_verdict: str = "PASS"


class CsvMappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping: dict[str, str]  # our field -> CSV column header
    default_lead_source: Opt2000 = None


class CsvRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Opt2000 = None
