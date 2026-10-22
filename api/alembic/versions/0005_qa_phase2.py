# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""QA fixes for Phase 2: a prefilter for the duplicate check (RISK-P2-01).

`accounts.name_grams` holds the sorted trigrams of the company name (legal-form words dropped,
Indic combining marks kept). A GIN index on it lets the duplicate check fetch only accounts that
can reach the similarity threshold instead of loading every name. It needs no extension, so it
works whether or not pg_trgm is installed (0002 still tries to create the pg_trgm index).

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op
from app.core.trigram import name_grams

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS name_grams text[]"))
    conn = op.get_bind()
    for rid, name in conn.execute(sa.text("SELECT id, name FROM accounts")).all():
        conn.execute(
            sa.text("UPDATE accounts SET name_grams = :g WHERE id = :i"),
            {"g": name_grams(name), "i": rid},
        )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS accounts_name_grams_gin ON accounts USING gin (name_grams)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS accounts_name_grams_null ON accounts (org_id) "
            "WHERE name_grams IS NULL"
        )
    )
    # a lead waiting in the Pool that is moved to another team closes its entry as 'moved'
    # (BUG-P2-22)
    op.execute(
        sa.text("ALTER TABLE lead_pool_entries DROP CONSTRAINT lead_pool_entries_left_reason_check")
    )
    op.execute(
        sa.text(
            "ALTER TABLE lead_pool_entries ADD CONSTRAINT lead_pool_entries_left_reason_check "
            "CHECK (left_reason IN ('assigned','deleted','moved'))"
        )
    )


def downgrade() -> None:
    raise NotImplementedError("Not reversible; restore from backup.")
