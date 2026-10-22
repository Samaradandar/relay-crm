# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""A meeting has a lifecycle: booked, held, postponed, cancelled or no-show.

Until now a meeting was just an activity dated in the future, so it could not be postponed or
cancelled with a reason, and a booked meeting whose date passed counted as "completed". The new
columns record what became of each meeting and why; a postponement keeps the old booking (marked
postponed, with the reason) and links the new booking to it, so the history is never lost.

Existing meetings are backfilled: dated in the future = booked, otherwise held.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

STATEMENTS = [
    """ALTER TABLE activities
        ADD COLUMN meeting_status text
            CHECK (meeting_status IN ('booked','held','postponed','cancelled','no_show')),
        ADD COLUMN status_reason_code text,
        ADD COLUMN status_reason text,
        ADD COLUMN status_at timestamptz,
        ADD COLUMN status_by uuid REFERENCES users(id),
        ADD COLUMN rescheduled_from uuid REFERENCES activities(id),
        ADD CONSTRAINT activities_meeting_status_only_meetings
            CHECK (meeting_status IS NULL OR type = 'meeting')""",
    """UPDATE activities
        SET meeting_status = CASE WHEN occurred_at > now() THEN 'booked' ELSE 'held' END
        WHERE type = 'meeting'""",
    "CREATE INDEX activities_meeting_status_idx ON activities (deal_id, meeting_status) "
    "WHERE meeting_status IS NOT NULL",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS activities_meeting_status_idx"))
    op.execute(
        sa.text(
            "ALTER TABLE activities DROP CONSTRAINT IF EXISTS "
            "activities_meeting_status_only_meetings, DROP COLUMN IF EXISTS rescheduled_from, "
            "DROP COLUMN IF EXISTS status_by, DROP COLUMN IF EXISTS status_at, "
            "DROP COLUMN IF EXISTS status_reason, DROP COLUMN IF EXISTS status_reason_code, "
            "DROP COLUMN IF EXISTS meeting_status"
        )
    )
