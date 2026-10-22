# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 8: google_event_links.html_link -- the Activity API response's `calendar.html_link`
(section 8) needs the real Google Calendar URL, not a reconstructed guess.

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE google_event_links ADD COLUMN html_link text"))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE google_event_links DROP COLUMN IF EXISTS html_link"))
