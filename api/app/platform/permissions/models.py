# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""permission_keys and role_permissions: the matrix as data (PRD 4.8)."""

from sqlalchemy import Boolean, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base


class PermissionKeyRow(Base):
    __tablename__ = "permission_keys"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    action_class: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")


class RolePermissionRow(Base):
    __tablename__ = "role_permissions"

    role: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, ForeignKey("permission_keys.key"), primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    own_deals_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    not_own_deals: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    routed_to: Mapped[str | None] = mapped_column(Text)
