# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 4 schema: customers, subscriptions, proposals, invoices, payments, minute packs, usage
ledger, overage, usage sync state, alert de-duplication, the GST setting and the catalog extension.

Conventions: money is integer paise (bigint), minutes are numeric(14,3), percentages are basis
points. Money history is append-only: relay_app cannot UPDATE or DELETE payments, allocations,
ledger rows or version copies.

Decisions beyond the PRD table (all additive; see the PR notes):
* `payments.kind` / `reverses_payment_id` / `reason` carry the negative correction entry.
* `payments.amount_paise` and `gst_paise` are GST-inclusive / the GST part, signed like the amount.
* `subscriptions.source_deal_id` is unique, so re-winning a deal can never make a second one.
* `proposals.status` also allows 'delivery_failed' (SES bounce or complaint).

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

ORG = "uuid NOT NULL REFERENCES orgs(id)"
FREQ = "('monthly','quarterly','yearly')"
AUDIT = """created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid,
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz"""


def _versions(table: str, parent: str, fk: str) -> str:
    """A full-copy history table: one row per saved state of the parent."""
    return f"""CREATE TABLE {table} (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        {fk} uuid NOT NULL REFERENCES {parent}(id),
        version_no integer NOT NULL CHECK (version_no >= 1),
        snapshot jsonb NOT NULL,
        reason text,
        changed_by uuid,
        changed_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE ({fk}, version_no))"""


STATEMENTS = [
    # ------------------------------------------------------------------ settings
    """CREATE TABLE billing_settings (
        org_id uuid PRIMARY KEY REFERENCES orgs(id),
        gst_rate_bp integer NOT NULL DEFAULT 1800 CHECK (gst_rate_bp BETWEEN 0 AND 10000),
        updated_at timestamptz NOT NULL DEFAULT now(),
        updated_by uuid)""",
    "INSERT INTO billing_settings (org_id) SELECT id FROM orgs",
    # ------------------------------------------------------------------ catalog extension
    """ALTER TABLE products
        ADD COLUMN pricing_model text NOT NULL DEFAULT 'per_minute'
            CHECK (pricing_model IN ('per_minute','saas')),
        ADD COLUMN pack_options jsonb NOT NULL DEFAULT '[]',
        ADD COLUMN subscription_fees jsonb NOT NULL DEFAULT '{}',
        ADD COLUMN allowed_contract_terms text[] NOT NULL
            DEFAULT ARRAY['monthly','quarterly','yearly']
            CHECK (allowed_contract_terms <@ ARRAY['monthly','quarterly','yearly']),
        ADD COLUMN allowed_billing_frequencies text[] NOT NULL
            DEFAULT ARRAY['monthly','quarterly','yearly']
            CHECK (allowed_billing_frequencies <@ ARRAY['monthly','quarterly','yearly']),
        ADD COLUMN one_time_fees jsonb NOT NULL DEFAULT '[]'""",
    # ------------------------------------------------------------------ customers
    f"""CREATE TABLE customers (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        account_id uuid NOT NULL REFERENCES accounts(id),
        account_owner_id uuid NOT NULL REFERENCES users(id),
        first_won_deal_id uuid NOT NULL REFERENCES deals(id),
        status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','churned')),
        voice_platform_customer_id text,
        {AUDIT})""",
    "CREATE UNIQUE INDEX customers_account_uniq ON customers (account_id)",
    "CREATE UNIQUE INDEX customers_voice_id_uniq ON customers (org_id, voice_platform_customer_id) "
    "WHERE voice_platform_customer_id IS NOT NULL AND deleted_at IS NULL",
    "CREATE INDEX customers_owner_idx ON customers (org_id, account_owner_id)",
    # ------------------------------------------------------------------ subscriptions
    f"""CREATE TABLE subscriptions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        customer_id uuid NOT NULL REFERENCES customers(id),
        source_deal_id uuid NOT NULL REFERENCES deals(id),
        status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','confirmed','ended')),
        pricing_model text NOT NULL CHECK (pricing_model IN ('per_minute','saas')),
        pack_price_paise bigint CHECK (pack_price_paise >= 0),
        pack_size_minutes numeric(14,3) CHECK (pack_size_minutes > 0),
        fee_paise bigint CHECK (fee_paise >= 0),
        billing_frequency text CHECK (billing_frequency IN {FREQ}),
        term_type text CHECK (term_type IN ('plan','contract')),
        contract_term text CHECK (contract_term IN {FREQ}),
        start_date date,
        renewal_date date,
        contract_end_date date,
        paid_through_date date,
        billing_contact_id uuid REFERENCES contacts(id),
        one_time_fees jsonb NOT NULL DEFAULT '[]',
        confirmed_by uuid REFERENCES users(id),
        confirmed_at timestamptz,
        version integer NOT NULL DEFAULT 1,
        {AUDIT},
        CHECK (pricing_model <> 'per_minute'
               OR (fee_paise IS NULL AND (status = 'draft'
                   OR (pack_price_paise IS NOT NULL AND pack_size_minutes IS NOT NULL)))),
        CHECK (pricing_model <> 'saas'
               OR (pack_price_paise IS NULL AND pack_size_minutes IS NULL
                   AND (status = 'draft' OR fee_paise IS NOT NULL))),
        CHECK (status <> 'confirmed'
               OR (billing_frequency IS NOT NULL AND term_type IS NOT NULL
                   AND contract_term IS NOT NULL AND start_date IS NOT NULL
                   AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)),
        CHECK (contract_end_date IS NULL OR start_date IS NULL
               OR contract_end_date >= start_date))""",
    "CREATE UNIQUE INDEX subscriptions_deal_uniq ON subscriptions (org_id, source_deal_id)",
    "CREATE INDEX subscriptions_customer_idx ON subscriptions (customer_id, status)",
    "CREATE INDEX subscriptions_pending_idx ON subscriptions (org_id, created_at) "
    "WHERE status = 'draft' AND deleted_at IS NULL",
    _versions("subscription_versions", "subscriptions", "subscription_id"),
    # ------------------------------------------------------------------ billing schedule, packs
    f"""CREATE TABLE billing_schedule (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        subscription_id uuid NOT NULL REFERENCES subscriptions(id),
        period_start date NOT NULL,
        period_end date NOT NULL,
        due_date date NOT NULL,
        expected_amount_paise bigint NOT NULL CHECK (expected_amount_paise >= 0),
        invoice_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (period_end >= period_start),
        UNIQUE (subscription_id, period_start))""",
    f"""CREATE TABLE minute_packs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        customer_id uuid NOT NULL REFERENCES customers(id),
        subscription_id uuid NOT NULL REFERENCES subscriptions(id),
        size_minutes numeric(14,3) NOT NULL CHECK (size_minutes > 0),
        price_paise bigint NOT NULL CHECK (price_paise >= 0),
        purchased_on date NOT NULL,
        expires_on date,
        source_deal_id uuid REFERENCES deals(id),
        invoice_id uuid,
        reason text,
        {AUDIT},
        CHECK (expires_on IS NULL OR expires_on >= purchased_on))""",
    "CREATE INDEX minute_packs_draw_idx ON minute_packs (customer_id, purchased_on, id) "
    "WHERE deleted_at IS NULL",
    # ------------------------------------------------------------------ invoices
    f"""CREATE TABLE invoices (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        customer_id uuid NOT NULL REFERENCES customers(id),
        subscription_id uuid REFERENCES subscriptions(id),
        schedule_id uuid REFERENCES billing_schedule(id),
        minute_pack_id uuid REFERENCES minute_packs(id),
        number text NOT NULL,
        issue_date date NOT NULL,
        due_date date NOT NULL,
        amount_ex_gst_paise bigint NOT NULL CHECK (amount_ex_gst_paise >= 0),
        gst_paise bigint NOT NULL CHECK (gst_paise >= 0),
        total_paise bigint NOT NULL CHECK (total_paise >= 0),
        file_id uuid REFERENCES files(id),
        version integer NOT NULL DEFAULT 1,
        status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','part_paid','paid','void')),
        void_reason text,
        {AUDIT},
        CHECK (total_paise = amount_ex_gst_paise + gst_paise),
        CHECK (schedule_id IS NULL OR minute_pack_id IS NULL))""",
    "CREATE UNIQUE INDEX invoices_number_uniq ON invoices (org_id, lower(number)) "
    "WHERE deleted_at IS NULL",
    "CREATE UNIQUE INDEX invoices_schedule_uniq ON invoices (schedule_id) "
    "WHERE schedule_id IS NOT NULL AND status <> 'void' AND deleted_at IS NULL",
    "CREATE UNIQUE INDEX invoices_pack_uniq ON invoices (minute_pack_id) "
    "WHERE minute_pack_id IS NOT NULL AND status <> 'void' AND deleted_at IS NULL",
    "CREATE INDEX invoices_customer_idx ON invoices (customer_id, due_date)",
    "ALTER TABLE billing_schedule ADD CONSTRAINT billing_schedule_invoice_fk "
    "FOREIGN KEY (invoice_id) REFERENCES invoices(id)",
    "ALTER TABLE minute_packs ADD CONSTRAINT minute_packs_invoice_fk "
    "FOREIGN KEY (invoice_id) REFERENCES invoices(id)",
    _versions("invoice_versions", "invoices", "invoice_id"),
    # ------------------------------------------------------------------ payments
    f"""CREATE TABLE payments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        customer_id uuid NOT NULL REFERENCES customers(id),
        kind text NOT NULL DEFAULT 'receipt' CHECK (kind IN ('receipt','correction')),
        amount_paise bigint NOT NULL CHECK (amount_paise <> 0),
        gst_paise bigint NOT NULL DEFAULT 0,
        received_on date NOT NULL,
        method text NOT NULL,
        reference text,
        proof_file_id uuid NOT NULL REFERENCES files(id),
        reverses_payment_id uuid REFERENCES payments(id),
        reason text,
        recorded_by uuid NOT NULL REFERENCES users(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK ((kind = 'receipt' AND amount_paise > 0 AND reverses_payment_id IS NULL)
               OR (kind = 'correction' AND amount_paise < 0
                   AND reverses_payment_id IS NOT NULL AND reason IS NOT NULL)),
        CHECK (gst_paise * amount_paise >= 0 AND abs(gst_paise) <= abs(amount_paise)))""",
    "CREATE UNIQUE INDEX payments_one_reversal ON payments (reverses_payment_id) "
    "WHERE reverses_payment_id IS NOT NULL",
    "CREATE INDEX payments_customer_idx ON payments (customer_id, received_on)",
    f"""CREATE TABLE payment_allocations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        payment_id uuid NOT NULL REFERENCES payments(id),
        invoice_id uuid NOT NULL REFERENCES invoices(id),
        amount_paise bigint NOT NULL CHECK (amount_paise <> 0),
        created_at timestamptz NOT NULL DEFAULT now())""",
    "CREATE INDEX payment_allocations_payment_idx ON payment_allocations (payment_id)",
    "CREATE INDEX payment_allocations_invoice_idx ON payment_allocations (invoice_id)",
    # an allocation can never take a payment past its amount or an invoice past its total.
    # SECURITY DEFINER: locking a row with FOR UPDATE needs UPDATE privilege, which relay_app
    # does not have on payments/invoices (append-only); the function runs as its owner instead.
    """CREATE FUNCTION payment_allocation_guard() RETURNS trigger AS $$
    DECLARE pay_amt bigint; pay_sum bigint; inv_total bigint; inv_sum bigint; inv_cust uuid;
            pay_cust uuid;
    BEGIN
        SELECT amount_paise, customer_id INTO pay_amt, pay_cust
            FROM payments WHERE id = NEW.payment_id FOR UPDATE;
        SELECT total_paise, customer_id INTO inv_total, inv_cust
            FROM invoices WHERE id = NEW.invoice_id FOR UPDATE;
        IF pay_cust IS DISTINCT FROM inv_cust THEN
            RAISE EXCEPTION 'payment % and invoice % belong to different customers',
                NEW.payment_id, NEW.invoice_id;
        END IF;
        SELECT COALESCE(SUM(amount_paise), 0) + NEW.amount_paise INTO pay_sum
            FROM payment_allocations WHERE payment_id = NEW.payment_id;
        SELECT COALESCE(SUM(amount_paise), 0) + NEW.amount_paise INTO inv_sum
            FROM payment_allocations WHERE invoice_id = NEW.invoice_id;
        IF (pay_amt > 0 AND (pay_sum < 0 OR pay_sum > pay_amt))
           OR (pay_amt < 0 AND (pay_sum > 0 OR pay_sum < pay_amt)) THEN
            RAISE EXCEPTION 'allocations would exceed payment %', NEW.payment_id;
        END IF;
        IF inv_sum < 0 OR inv_sum > inv_total THEN
            RAISE EXCEPTION 'allocations would exceed invoice %', NEW.invoice_id;
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public""",
    "CREATE TRIGGER payment_allocation_guard BEFORE INSERT ON payment_allocations "
    "FOR EACH ROW EXECUTE FUNCTION payment_allocation_guard()",
    # an invoice edit cannot take the total below what is already allocated to it
    """CREATE FUNCTION invoice_total_guard() RETURNS trigger AS $$
    DECLARE allocated bigint;
    BEGIN
        IF NEW.total_paise <> OLD.total_paise THEN
            SELECT COALESCE(SUM(amount_paise), 0) INTO allocated
                FROM payment_allocations WHERE invoice_id = NEW.id;
            IF NEW.total_paise < allocated THEN
                RAISE EXCEPTION 'invoice % total is below the % already allocated',
                    NEW.id, allocated;
            END IF;
        END IF;
        RETURN NEW;
    END $$ LANGUAGE plpgsql""",
    "CREATE TRIGGER invoice_total_guard BEFORE UPDATE OF total_paise ON invoices "
    "FOR EACH ROW EXECUTE FUNCTION invoice_total_guard()",
    # ------------------------------------------------------------------ usage and ledger
    f"""CREATE TABLE usage_records (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        customer_id uuid NOT NULL REFERENCES customers(id),
        source_record_id text NOT NULL,
        occurred_at timestamptz NOT NULL,
        minutes numeric(14,3) NOT NULL CHECK (minutes >= 0),
        received_at timestamptz NOT NULL DEFAULT now(),
        revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
        UNIQUE (org_id, source_record_id))""",
    "CREATE INDEX usage_records_customer_idx ON usage_records (customer_id, occurred_at)",
    f"""CREATE TABLE unmatched_usage (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        source_record_id text NOT NULL,
        voice_customer_id text NOT NULL,
        occurred_at timestamptz NOT NULL,
        minutes numeric(14,3) NOT NULL CHECK (minutes >= 0),
        revision integer NOT NULL DEFAULT 1,
        received_at timestamptz NOT NULL DEFAULT now(),
        resolved_at timestamptz,
        UNIQUE (org_id, source_record_id))""",
    f"""CREATE TABLE minute_ledger (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        customer_id uuid NOT NULL REFERENCES customers(id),
        usage_record_id uuid REFERENCES usage_records(id),
        minute_pack_id uuid REFERENCES minute_packs(id),
        minutes numeric(14,3) NOT NULL CHECK (minutes <> 0),
        kind text NOT NULL CHECK (kind IN ('draw','correction','overage')),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (kind <> 'overage' OR minute_pack_id IS NULL),
        CHECK (kind <> 'draw' OR minutes > 0))""",
    "CREATE INDEX minute_ledger_pack_idx ON minute_ledger (minute_pack_id)",
    "CREATE INDEX minute_ledger_customer_idx ON minute_ledger (customer_id, created_at)",
    f"""CREATE TABLE overage_items (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        customer_id uuid NOT NULL REFERENCES customers(id),
        minutes numeric(14,3) NOT NULL CHECK (minutes <> 0),
        reason text NOT NULL CHECK (reason IN ('no_pack','pack_expired','late_correction')),
        status text NOT NULL DEFAULT 'unbilled' CHECK (status IN ('unbilled','billed','waived')),
        invoice_id uuid REFERENCES invoices(id),
        usage_record_id uuid REFERENCES usage_records(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CHECK (status <> 'billed' OR invoice_id IS NOT NULL))""",
    "CREATE INDEX overage_items_unbilled_idx ON overage_items (org_id, customer_id) "
    "WHERE status = 'unbilled'",
    """CREATE TABLE usage_sync_state (
        org_id uuid PRIMARY KEY REFERENCES orgs(id),
        cursor text,
        last_success_at timestamptz,
        last_attempt_at timestamptz,
        last_error text,
        consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
        updated_at timestamptz NOT NULL DEFAULT now())""",
    "INSERT INTO usage_sync_state (org_id) SELECT id FROM orgs",
    # ------------------------------------------------------------------ proposals
    f"""CREATE TABLE proposal_templates (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        name text NOT NULL,
        body text NOT NULL,
        terms_html text NOT NULL DEFAULT '',
        version integer NOT NULL CHECK (version >= 1),
        active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid,
        UNIQUE (org_id, name, version))""",
    f"""CREATE TABLE proposals (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        deal_id uuid NOT NULL REFERENCES deals(id),
        version_no integer NOT NULL CHECK (version_no >= 1),
        template_version integer,
        content jsonb NOT NULL DEFAULT '{{}}',
        status text NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft','sent','accepted','changes_requested','rejected',
                              'delivery_failed')),
        pdf_file_id uuid REFERENCES files(id),
        sent_at timestamptz,
        sent_to text,
        email_message_id text,
        feedback text,
        {AUDIT},
        UNIQUE (deal_id, version_no))""",
    "CREATE INDEX proposals_message_idx ON proposals (email_message_id) "
    "WHERE email_message_id IS NOT NULL",
    f"""CREATE TABLE proposal_items (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        proposal_id uuid NOT NULL REFERENCES proposals(id),
        product_id uuid REFERENCES products(id),
        description text NOT NULL,
        quantity integer NOT NULL CHECK (quantity > 0),
        unit_price_paise bigint NOT NULL CHECK (unit_price_paise >= 0),
        discount_bp integer NOT NULL DEFAULT 0 CHECK (discount_bp BETWEEN 0 AND 10000),
        line_total_paise bigint NOT NULL CHECK (line_total_paise >= 0))""",
    "CREATE INDEX proposal_items_proposal_idx ON proposal_items (proposal_id)",
    # ------------------------------------------------------------------ alert de-duplication
    # customer_id has no foreign key: an org-level alert (usage sync failing) has no customer,
    # and stores the all-zero uuid instead, so the dedup unique index always applies.
    f"""CREATE TABLE billing_alerts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        org_id {ORG},
        customer_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
        alert_type text NOT NULL,
        ref_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
        alert_date date NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (org_id, customer_id, alert_type, ref_id, alert_date))""",
    # ------------------------------------------------------------------ grants
    """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO relay_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO relay_app;
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM relay_app;
        REVOKE UPDATE, DELETE ON activity_versions FROM relay_app;
        REVOKE DELETE ON pipeline_versions, deal_stage_entries, export_log, activities,
            documents, deal_products FROM relay_app;
        REVOKE UPDATE, DELETE, TRUNCATE ON payments, payment_allocations, minute_ledger,
            subscription_versions, invoice_versions FROM relay_app;
        REVOKE DELETE ON customers, subscriptions, invoices, minute_packs, usage_records,
            overage_items, proposals, proposal_items, billing_schedule FROM relay_app;
        END IF; END $$""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    raise NotImplementedError("Not reversible; restore from backup.")
