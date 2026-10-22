# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Test-only scoped model, standing in for deals (which arrive in Phase 2)."""

import uuid

from sqlalchemy import ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import Base, org_fk, uuid_pk
from app.platform.permissions.scope import ScopedEntity, register_entity


class Widget(Base):
    __tablename__ = "widgets"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False, default="w")


register_entity("widget", ScopedEntity(Widget))


async def create_widget_table(engine: AsyncEngine) -> None:
    async with engine.begin() as c:
        await c.execute(
            text(
                "CREATE TABLE IF NOT EXISTS widgets (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
                " org_id uuid NOT NULL REFERENCES orgs(id),"
                " owner_id uuid REFERENCES users(id), name text NOT NULL DEFAULT 'w')"
            )
        )
        await c.execute(text("GRANT ALL ON widgets TO relay_app"))
