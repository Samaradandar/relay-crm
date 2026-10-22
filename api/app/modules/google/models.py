# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""SQLAlchemy tables owned by this module: a rep's Google Calendar/Gmail connection, Relay<->
Google event links, calendar sync state, logged Gmail threads/messages and unlinked-event
suggestions.

Built in: Phase 8 (side track).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, org_fk, uuid_pk


def _uuid(fk: str | None = None, *, nullable: bool = True) -> Mapped[Any]:
    col = UUID(as_uuid=True)
    if fk:
        return mapped_column(col, ForeignKey(fk), nullable=nullable)
    return mapped_column(col, nullable=nullable)


class GoogleAccount(Base):
    __tablename__ = "google_accounts"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    google_email: Mapped[str] = mapped_column(Text, nullable=False)
    granted_scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="connected")
    connected_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    disconnected_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    last_success_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    last_error: Mapped[str | None] = mapped_column(Text)
    reconnect_notified_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class GoogleEventLink(Base):
    __tablename__ = "google_event_links"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    activity_id: Mapped[uuid.UUID] = _uuid("activities.id", nullable=False)
    deal_id: Mapped[uuid.UUID] = _uuid("deals.id", nullable=False)
    user_id: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    calendar_id: Mapped[str] = mapped_column(Text, nullable=False, default="primary")
    google_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    html_link: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    last_direction: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class CalendarSyncState(Base):
    __tablename__ = "calendar_sync_state"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    calendar_id: Mapped[str] = mapped_column(Text, nullable=False, default="primary")
    sync_token: Mapped[str | None] = mapped_column(Text)
    channel_id: Mapped[str | None] = mapped_column(Text)
    channel_token: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    channel_expiry: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    last_full_sync_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    last_push_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class GmailLoggedThread(Base):
    __tablename__ = "gmail_logged_threads"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = _uuid("deals.id", nullable=False)
    activity_id: Mapped[uuid.UUID] = _uuid("activities.id", nullable=False)
    user_id: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    gmail_thread_id: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    logged_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    logged_by: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    deleted_reason: Mapped[str | None] = mapped_column(Text)


class GmailLoggedMessage(Base):
    __tablename__ = "gmail_logged_messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    thread_row_id: Mapped[uuid.UUID] = _uuid("gmail_logged_threads.id", nullable=False)
    gmail_message_id: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    from_email: Mapped[str] = mapped_column(Text, nullable=False)
    to_emails: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    cc_emails: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    sent_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)


class CalendarSuggestion(Base):
    __tablename__ = "calendar_suggestions"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    google_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    calendar_id: Mapped[str] = mapped_column(Text, nullable=False, default="primary")
    deal_id: Mapped[uuid.UUID | None] = _uuid("deals.id")
    contact_email: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    linked_activity_id: Mapped[uuid.UUID | None] = _uuid("activities.id")
    decided_by: Mapped[uuid.UUID | None] = _uuid("users.id")
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
