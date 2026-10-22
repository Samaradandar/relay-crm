# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 3 schema: versioned pipelines, stage engine, criteria, activities, catalog, documents,
exports; extends deals and next steps. Seeds New business version 1 for every existing org and
puts existing deals into it.

Revision ID: 0003
Revises: 0002
"""

import json

import sqlalchemy as sa

from alembic import op
from app.modules.pipeline.seeds.new_business import seed_statements

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TEXT_UUID = "uuid NOT NULL REFERENCES orgs(id)"

STATEMENTS = [
    # ------------------------------------------------------------------ pipeline configuration
    f"""CREATE TABLE pipelines (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        key text NOT NULL CHECK (key IN ('new_business','existing_customer')),
        name text NOT NULL,
        UNIQUE (org_id, key))""",
    f"""CREATE TABLE pipeline_versions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        pipeline_id uuid NOT NULL REFERENCES pipelines(id),
        version_no integer NOT NULL,
        status text NOT NULL CHECK (status IN ('draft','published','archived')),
        created_by uuid,
        published_by uuid,
        published_at timestamptz,
        notes text,
        diff_summary jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (pipeline_id, version_no))""",
    "CREATE UNIQUE INDEX pipeline_one_draft ON pipeline_versions (pipeline_id) "
    "WHERE status = 'draft'",
    "CREATE UNIQUE INDEX pipeline_one_published ON pipeline_versions (pipeline_id) "
    "WHERE status = 'published'",
    f"""CREATE TABLE stages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        version_id uuid NOT NULL REFERENCES pipeline_versions(id),
        key text NOT NULL,
        name text NOT NULL,
        position integer NOT NULL,
        probability_pct integer NOT NULL CHECK (probability_pct BETWEEN 0 AND 100),
        is_entry boolean NOT NULL DEFAULT false,
        is_final boolean NOT NULL DEFAULT false,
        won_on_entry boolean NOT NULL DEFAULT false,
        is_conditional boolean NOT NULL DEFAULT false,
        display_group text NOT NULL,
        entry_checks jsonb NOT NULL DEFAULT '[]',
        UNIQUE (version_id, key))""",
    f"""CREATE TABLE stage_transitions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        version_id uuid NOT NULL REFERENCES pipeline_versions(id),
        from_stage_key text NOT NULL,
        to_stage_key text NOT NULL,
        kind text NOT NULL CHECK (kind IN ('forward','backward','conditional')),
        UNIQUE (version_id, from_stage_key, to_stage_key))""",
    f"""CREATE TABLE stage_criteria (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        version_id uuid NOT NULL REFERENCES pipeline_versions(id),
        stage_key text NOT NULL,
        key text NOT NULL,
        label text NOT NULL,
        help_text text,
        position integer NOT NULL,
        proof_type text NOT NULL,
        proof_config jsonb NOT NULL DEFAULT '{{}}',
        UNIQUE (version_id, stage_key, key))""",
    # a published version is frozen: only a draft's children can change
    """CREATE FUNCTION frozen_version_guard() RETURNS trigger AS $$
    DECLARE vid uuid; st text;
    BEGIN
        vid := CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
        SELECT status INTO st FROM pipeline_versions WHERE id = vid;
        IF st IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'pipeline version % is % and cannot be changed', vid, st;
        END IF;
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END $$ LANGUAGE plpgsql""",
    "CREATE TRIGGER stages_frozen BEFORE INSERT OR UPDATE OR DELETE ON stages "
    "FOR EACH ROW EXECUTE FUNCTION frozen_version_guard()",
    "CREATE TRIGGER transitions_frozen BEFORE INSERT OR UPDATE OR DELETE ON stage_transitions "
    "FOR EACH ROW EXECUTE FUNCTION frozen_version_guard()",
    "CREATE TRIGGER criteria_frozen BEFORE INSERT OR UPDATE OR DELETE ON stage_criteria "
    "FOR EACH ROW EXECUTE FUNCTION frozen_version_guard()",
    f"""CREATE TABLE custom_field_defs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        entity text NOT NULL CHECK (entity IN ('deal','contact','account')),
        key text NOT NULL,
        label text NOT NULL,
        type text NOT NULL CHECK (type IN
            ('text','number','date','dropdown','yes_no','file','contact')),
        options jsonb NOT NULL DEFAULT '[]',
        active boolean NOT NULL DEFAULT true,
        UNIQUE (org_id, entity, key))""",
    f"""CREATE TABLE lost_reasons (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        label text NOT NULL,
        position integer NOT NULL DEFAULT 0,
        active boolean NOT NULL DEFAULT true,
        UNIQUE (org_id, label))""",
    f"""CREATE TABLE fit_items (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        label text NOT NULL,
        position integer NOT NULL DEFAULT 0,
        active boolean NOT NULL DEFAULT true)""",
    # ------------------------------------------------------------------ deals
    "ALTER TABLE deals DROP CONSTRAINT IF EXISTS deals_pipeline_check",
    "ALTER TABLE deals ADD CONSTRAINT deals_pipeline_check "
    "CHECK (pipeline IN ('new_business','existing_customer'))",
    """ALTER TABLE deals
        ADD COLUMN pipeline_version_id uuid REFERENCES pipeline_versions(id),
        ADD COLUMN stage_entered_at timestamptz NOT NULL DEFAULT now(),
        ADD COLUMN revision_round integer NOT NULL DEFAULT 1,
        ADD COLUMN won_at timestamptz,
        ADD COLUMN lost_at timestamptz,
        ADD COLUMN lost_reason text,
        ADD COLUMN lost_notes text,
        ADD COLUMN lost_from_stage_key text,
        ADD COLUMN expected_close_date date,
        ADD COLUMN expected_close_reason text,
        ADD COLUMN value_paise bigint NOT NULL DEFAULT 0 CHECK (value_paise >= 0),
        ADD COLUMN pricing_model text,
        ADD COLUMN plan text,
        ADD COLUMN expected_minutes_per_month integer CHECK (expected_minutes_per_month >= 0),
        ADD COLUMN use_case text,
        ADD COLUMN pain_point text,
        ADD COLUMN fit_checklist jsonb NOT NULL DEFAULT '{}',
        ADD COLUMN data jsonb NOT NULL DEFAULT '{}',
        ADD COLUMN custom_fields jsonb NOT NULL DEFAULT '{}',
        ADD COLUMN last_activity_at timestamptz,
        ADD COLUMN health text NOT NULL DEFAULT 'on_track'
            CHECK (health IN ('on_track','at_risk','stale')),
        ADD COLUMN flags jsonb NOT NULL DEFAULT '{}',
        ADD COLUMN deleted_reason text""",
    "CREATE INDEX deals_board_idx ON deals (org_id, status, stage_key, team_id) "
    "WHERE deleted_at IS NULL",
    "CREATE INDEX deals_health_idx ON deals (org_id, health) WHERE status = 'open' "
    "AND deleted_at IS NULL",
    "ALTER TABLE accounts ADD COLUMN custom_fields jsonb NOT NULL DEFAULT '{}'",
    "ALTER TABLE contacts ADD COLUMN custom_fields jsonb NOT NULL DEFAULT '{}'",
    # ------------------------------------------------------------------ stage engine
    f"""CREATE TABLE deal_stage_entries (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        deal_id uuid NOT NULL REFERENCES deals(id),
        stage_key text NOT NULL,
        version_id uuid NOT NULL REFERENCES pipeline_versions(id),
        revision_round integer NOT NULL DEFAULT 1,
        entered_at timestamptz NOT NULL DEFAULT now(),
        entered_by uuid,
        exited_at timestamptz,
        exit_kind text CHECK (exit_kind IN
            ('forward','backward','override','skip','lost','reopen','won','delete')),
        reason text,
        checklist_snapshot jsonb NOT NULL DEFAULT '[]')""",
    "CREATE UNIQUE INDEX stage_entry_one_open ON deal_stage_entries (deal_id) "
    "WHERE exited_at IS NULL",
    "CREATE INDEX stage_entry_deal_idx ON deal_stage_entries (deal_id, entered_at)",
    "CREATE INDEX stage_entry_stage_idx ON deal_stage_entries (org_id, stage_key, entered_at)",
    f"""CREATE TABLE deal_criteria (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        deal_id uuid NOT NULL REFERENCES deals(id),
        entry_id uuid NOT NULL REFERENCES deal_stage_entries(id),
        criterion_key text NOT NULL,
        ticked_by uuid,
        ticked_at timestamptz NOT NULL DEFAULT now(),
        proof_ref jsonb,
        verify_status text NOT NULL DEFAULT 'pending'
            CHECK (verify_status IN ('pending','verified','rejected','overridden')),
        verified_by uuid,
        verified_at timestamptz,
        verify_reason text,
        proof_missing boolean NOT NULL DEFAULT false,
        UNIQUE (entry_id, criterion_key))""",
    "CREATE INDEX deal_criteria_deal_idx ON deal_criteria (deal_id)",
    # ------------------------------------------------------------------ activities
    f"""CREATE TABLE activities (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        deal_id uuid NOT NULL REFERENCES deals(id),
        account_id uuid NOT NULL REFERENCES accounts(id),
        type text NOT NULL CHECK (type IN
            ('call','email','meeting','discovery','walkthrough','kickoff','note')),
        occurred_at timestamptz NOT NULL,
        two_way boolean NOT NULL DEFAULT false,
        attendees jsonb NOT NULL DEFAULT '[]',
        discussed text,
        requirements text,
        objections text,
        decisions text,
        actions text,
        version_no integer NOT NULL DEFAULT 1,
        created_by uuid NOT NULL,
        attributed_to uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz,
        deleted_reason text)""",
    "CREATE INDEX activities_deal_idx ON activities (deal_id, occurred_at DESC)",
    "CREATE INDEX activities_account_idx ON activities (account_id, occurred_at DESC)",
    "CREATE INDEX activities_user_idx ON activities (org_id, attributed_to, occurred_at DESC)",
    """CREATE TABLE activity_versions (
        activity_id uuid NOT NULL REFERENCES activities(id),
        version_no integer NOT NULL,
        body jsonb NOT NULL,
        edited_by uuid NOT NULL,
        edited_at timestamptz NOT NULL DEFAULT now(),
        reason text,
        PRIMARY KEY (activity_id, version_no))""",
    """ALTER TABLE next_steps
        ADD COLUMN lane text NOT NULL DEFAULT 'your_move'
            CHECK (lane IN ('your_move','waiting_on_them')),
        ADD COLUMN lane_since timestamptz NOT NULL DEFAULT now(),
        ADD COLUMN snoozed_until date,
        ADD COLUMN snooze_count integer NOT NULL DEFAULT 0,
        ADD COLUMN assigned_by uuid""",
    # ------------------------------------------------------------------ catalog and documents
    f"""CREATE TABLE products (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        name text NOT NULL,
        description text,
        unit text NOT NULL CHECK (unit IN ('per_minute','fixed')),
        list_price_paise bigint NOT NULL CHECK (list_price_paise >= 0),
        floor_price_paise bigint NOT NULL CHECK (floor_price_paise >= 0),
        active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CHECK (floor_price_paise <= list_price_paise))""",
    "CREATE UNIQUE INDEX products_name_uniq ON products (org_id, lower(name))",
    f"""CREATE TABLE deal_products (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        deal_id uuid NOT NULL REFERENCES deals(id),
        product_id uuid NOT NULL REFERENCES products(id),
        quantity integer NOT NULL CHECK (quantity > 0),
        unit_price_paise bigint NOT NULL CHECK (unit_price_paise >= 0),
        discount_pct numeric(5,2) NOT NULL DEFAULT 0 CHECK (discount_pct BETWEEN 0 AND 100),
        line_total_paise bigint NOT NULL CHECK (line_total_paise >= 0),
        discount_status text NOT NULL DEFAULT 'none'
            CHECK (discount_status IN ('none','pending','approved')),
        pending_discount_pct numeric(5,2),
        approval_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid,
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz)""",
    "CREATE INDEX deal_products_deal_idx ON deal_products (deal_id) WHERE deleted_at IS NULL",
    f"""CREATE TABLE documents (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        deal_id uuid NOT NULL REFERENCES deals(id),
        kind text NOT NULL CHECK (kind IN ('proposal','agreement','invoice')),
        version_no integer NOT NULL DEFAULT 1,
        status text NOT NULL CHECK (status IN ('draft','sent','countersigned','terminated')),
        sent_at timestamptz,
        file_id uuid REFERENCES files(id),
        amount_paise bigint CHECK (amount_paise >= 0),
        number text,
        notes text,
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid,
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz,
        deleted_reason text)""",
    "CREATE INDEX documents_deal_idx ON documents (deal_id, kind) WHERE deleted_at IS NULL",
    f"""CREATE TABLE export_log (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {TEXT_UUID},
        user_id uuid NOT NULL REFERENCES users(id),
        entity text NOT NULL,
        filters jsonb NOT NULL DEFAULT '{{}}',
        row_count integer,
        reason text NOT NULL,
        status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','done','failed')),
        file_id uuid REFERENCES files(id),
        created_at timestamptz NOT NULL DEFAULT now())""",
    """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO relay_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO relay_app;
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM relay_app;
        REVOKE UPDATE, DELETE ON activity_versions FROM relay_app;
        REVOKE DELETE ON pipeline_versions, deal_stage_entries, export_log, activities,
            documents, deal_products FROM relay_app;
        END IF; END $$""",
]


def upgrade() -> None:
    conn = op.get_bind()
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))
    org_ids = [r[0] for r in conn.execute(sa.text("SELECT id FROM orgs"))]
    for org_id in org_ids:
        for sql, params in seed_statements(org_id):
            conn.execute(sa.text(sql), params)
    # Existing deals join New business version 1 at the stage they are in, with its checklist.
    deals = conn.execute(
        sa.text(
            "SELECT d.id, d.org_id, d.stage_key, d.created_at, v.id AS vid FROM deals d "
            "JOIN pipelines p ON p.org_id = d.org_id AND p.key = 'new_business' "
            "JOIN pipeline_versions v ON v.pipeline_id = p.id AND v.status = 'published'"
        )
    ).all()
    for d in deals:
        crit = conn.execute(
            sa.text(
                "SELECT key, label, proof_type, proof_config FROM stage_criteria "
                "WHERE version_id = :v AND stage_key = :s ORDER BY position"
            ),
            {"v": d.vid, "s": d.stage_key},
        ).all()
        snap = [
            {
                "key": c.key,
                "label": c.label,
                "proof_type": c.proof_type,
                "proof_config": c.proof_config,
            }
            for c in crit
        ]
        conn.execute(
            sa.text(
                "UPDATE deals SET pipeline_version_id = :v, stage_entered_at = :t WHERE id = :d"
            ),
            {"v": d.vid, "t": d.created_at, "d": d.id},
        )
        conn.execute(
            sa.text(
                "INSERT INTO deal_stage_entries (org_id, deal_id, stage_key, version_id, "
                "entered_at, checklist_snapshot) VALUES (:o, :d, :s, :v, :t, CAST(:snap AS jsonb))"
            ),
            {"o": d.org_id, "d": d.id, "s": d.stage_key, "v": d.vid, "t": d.created_at,
             "snap": json.dumps(snap)},
        )  # fmt: skip


def downgrade() -> None:
    raise NotImplementedError("Not reversible; restore from backup.")
