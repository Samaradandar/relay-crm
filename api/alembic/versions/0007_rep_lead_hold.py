# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""A lead created by a sales rep waits for the Team Leader instead of being auto-assigned.

* `assignment_settings.rep_lead_hold_minutes` (default 120): how long the Team Leader has before
  the lead falls back to automatic round robin.
* `next_steps.auto_generated`: the step came from the pipeline, not from a person.
* `lead_pool_entries.rep_hold`: this waiting entry is a rep-created lead held for the Team Leader
  (`manual_hold` keeps the engine away; `rep_hold` says why and lets the job expire it).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

STATEMENTS = [
    "ALTER TABLE assignment_settings ADD COLUMN IF NOT EXISTS "
    "rep_lead_hold_minutes integer NOT NULL DEFAULT 120",
    "ALTER TABLE assignment_settings ADD CONSTRAINT assignment_settings_hold_range "
    "CHECK (rep_lead_hold_minutes BETWEEN 1 AND 10080)",
    "ALTER TABLE lead_pool_entries ADD COLUMN IF NOT EXISTS "
    "rep_hold boolean NOT NULL DEFAULT false",
    # a next step the system wrote from the pipeline (not chosen by a person): a later stage
    # move may replace it; a person's own next step is never replaced automatically
    "ALTER TABLE next_steps ADD COLUMN IF NOT EXISTS auto_generated boolean NOT NULL DEFAULT false",
    "CREATE INDEX IF NOT EXISTS lead_pool_entries_rep_hold ON lead_pool_entries "
    "(org_id, entered_at) WHERE rep_hold AND left_at IS NULL",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    raise NotImplementedError("Not reversible; restore from backup.")
