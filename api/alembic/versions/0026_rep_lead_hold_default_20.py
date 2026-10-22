# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Shorten the default rep-lead hold window from 120 to 20 minutes (product decision): a Team
Leader now has 20 minutes to decide before a rep's own lead falls back to automatic round robin.
Still per-org configurable via PUT /settings/assignment -- this only changes the default for a
newly-created org and any org that never touched the setting.

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE assignment_settings ALTER COLUMN rep_lead_hold_minutes SET DEFAULT 20")
    )
    op.execute(
        sa.text(
            "UPDATE assignment_settings SET rep_lead_hold_minutes = 20 "
            "WHERE rep_lead_hold_minutes = 120"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE assignment_settings ALTER COLUMN rep_lead_hold_minutes SET DEFAULT 120"
        )
    )
    op.execute(
        sa.text(
            "UPDATE assignment_settings SET rep_lead_hold_minutes = 120 "
            "WHERE rep_lead_hold_minutes = 20"
        )
    )
