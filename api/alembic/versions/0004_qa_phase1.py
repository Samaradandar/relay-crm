# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""QA fixes for Phase 1: insert-only audit anchors (BUG-P1-10), e-mail dedupe index (BUG-P1-12).

Every audit row gets a copy of (org, seq, row_hash) in `audit_anchor`. The app role can only
INSERT and SELECT there and a trigger stops UPDATE/DELETE/TRUNCATE even for the owner without
first disabling it, so deleting the newest audit rows, wiping the trail or rewriting history and
re-computing the chain is caught by `verify_chain`.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

STATEMENTS = [
    """CREATE TABLE audit_anchor (
        org_id uuid NOT NULL,
        seq bigint NOT NULL,
        row_hash text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (org_id, seq))""",
    """INSERT INTO audit_anchor (org_id, seq, row_hash)
        SELECT org_id, seq, row_hash FROM audit_log""",
    """CREATE FUNCTION audit_anchor_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'audit_anchor is append-only'; END $$""",
    """CREATE TRIGGER audit_anchor_no_update_delete BEFORE UPDATE OR DELETE ON audit_anchor
        FOR EACH ROW EXECUTE FUNCTION audit_anchor_immutable()""",
    """CREATE TRIGGER audit_anchor_no_truncate BEFORE TRUNCATE ON audit_anchor
        FOR EACH STATEMENT EXECUTE FUNCTION audit_anchor_immutable()""",
    """CREATE INDEX outbox_email_dedupe_idx ON outbox_events ((payload->>'dedupe'))
        WHERE type = 'email.send'""",
    """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
        GRANT SELECT, INSERT ON audit_anchor TO relay_app;
        REVOKE UPDATE, DELETE, TRUNCATE, TRIGGER, REFERENCES ON audit_anchor FROM relay_app;
        END IF; END $$""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    raise NotImplementedError("Not reversible; restore from backup.")
