# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Documents the company receives: the advance, pre-requisite and technical documents.

The documents table only knew proposals, agreements and invoices. Closure now asks for the advance
that was received (with its receipt) and Onboarding for the pre-requisite document and the technical
document, so those become document kinds with a 'received' status.

Revision ID: 0030
Revises: 0029
"""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_kind_check")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT documents_kind_check CHECK (kind IN "
        "('proposal','agreement','invoice','advance','prerequisite','technical'))"
    )
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT documents_status_check CHECK (status IN "
        "('draft','sent','countersigned','terminated','received'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM documents WHERE kind IN ('advance','prerequisite','technical')")
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_kind_check")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT documents_kind_check CHECK "
        "(kind IN ('proposal','agreement','invoice'))"
    )
    op.execute("ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_status_check")
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT documents_status_check CHECK "
        "(status IN ('draft','sent','countersigned','terminated'))"
    )
