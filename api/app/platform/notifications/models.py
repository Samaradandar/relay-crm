# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""notifications (in-app) and per-user, per-type channel settings."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    link: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = created_at_col()


class NotificationSetting(Base):
    __tablename__ = "notification_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    type: Mapped[str] = mapped_column(Text, primary_key=True)
    in_app: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class NotificationPreference(Base):
    """DEAD TABLE -- not read or written anywhere. Added in PR1 as a maybe-org-scoped alternative
    to `NotificationSetting` above, for a settings screen that had not been built yet.

    Resolved in P5-08: `NotificationSetting` is kept as the single source of truth, because it
    was already fully wired into `notify()`'s recipient resolution (both the in_app and email
    channels, for every notification type, not just Phase 5's) before this table existed, and the
    settings screen built in this PR (`GET/PUT /me/notification-preferences`) reads and writes it
    instead. `NotificationSetting` not having `org_id` is not a real gap: it is keyed by
    (user_id, type), and a user belongs to exactly one org, so org-scoping falls out of the
    `user_id` join everywhere it is used. This table is left in place, unused, rather than dropped,
    to avoid a destructive migration in this PR; a later migration may remove it."""

    __tablename__ = "notification_preferences"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
