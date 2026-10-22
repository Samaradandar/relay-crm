# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""QA fixes for Phase 3: version freezing cannot be bypassed (BUG-P3-02, BUG-P3-03).

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

STATEMENTS = [
    # a row may not be moved out of (OLD) or into (NEW) a version that is not a draft
    """CREATE OR REPLACE FUNCTION frozen_version_guard() RETURNS trigger AS $$
    DECLARE vid uuid; st text;
    BEGIN
        FOREACH vid IN ARRAY (CASE TG_OP
            WHEN 'DELETE' THEN ARRAY[OLD.version_id]
            WHEN 'INSERT' THEN ARRAY[NEW.version_id]
            ELSE ARRAY[OLD.version_id, NEW.version_id] END)
        LOOP
            SELECT status INTO st FROM pipeline_versions WHERE id = vid;
            IF st IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'pipeline version % is % and cannot be changed', vid, st;
            END IF;
        END LOOP;
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$ LANGUAGE plpgsql""",
    # status only moves draft -> published|archived and published -> archived
    """CREATE FUNCTION pipeline_version_status_guard() RETURNS trigger AS $$
    BEGIN
        IF OLD.status <> 'draft' AND NEW.status = 'draft' THEN
            RAISE EXCEPTION 'pipeline version % is % and cannot become a draft',
                OLD.id, OLD.status;
        END IF;
        IF OLD.status = 'archived' AND NEW.status <> 'archived' THEN
            RAISE EXCEPTION 'pipeline version % is archived and cannot be revived', OLD.id;
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql""",
    "CREATE TRIGGER pipeline_version_status BEFORE UPDATE ON pipeline_versions "
    "FOR EACH ROW EXECUTE FUNCTION pipeline_version_status_guard()",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    raise NotImplementedError("Not reversible; restore from backup.")
