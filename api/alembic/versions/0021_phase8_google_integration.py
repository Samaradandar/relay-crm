# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 8 (side track, ST-01..05) schema: a rep's own Google Calendar/Gmail connection (separate
from Cognito's "Sign in with Google"), the links between Relay activities and Google events,
calendar sync state and channel tracking, logged Gmail threads/messages, and unlinked-event
suggestions.

This PR is schema only -- no service/router writes yet, mirroring how Phases 5-7 opened. The
real Google Cloud project, OAuth consent screen and KMS key are not provisioned yet (confirmed
with Samar); see app/core/config.py and app/modules/google/client.py for how that's handled.

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

ORG = "uuid NOT NULL REFERENCES orgs(id)"

STATEMENTS = [
    # ---------------------------------------------------------------------------- google_accounts
    f"""CREATE TABLE google_accounts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    user_id uuid NOT NULL REFERENCES users(id),
    google_email text NOT NULL,
    granted_scopes text[] NOT NULL DEFAULT '{{}}',
    encrypted_refresh_token text,
    status text NOT NULL DEFAULT 'connected' CHECK (status IN
        ('connected', 'needs_reconnect', 'disconnected')),
    connected_at timestamptz NOT NULL DEFAULT now(),
    disconnected_at timestamptz,
    last_success_at timestamptz,
    last_error text,
    reconnect_notified_at timestamptz,
    UNIQUE (user_id))""",
    # one connected Google account per Relay user (above); this also stops two users claiming
    # the same Google mailbox while both are actively connected.
    "CREATE UNIQUE INDEX google_accounts_email_active_uniq ON google_accounts (org_id, google_email) "
    "WHERE status != 'disconnected'",
    # ------------------------------------------------------------------------- google_event_links
    f"""CREATE TABLE google_event_links (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    activity_id uuid NOT NULL REFERENCES activities(id),
    deal_id uuid NOT NULL REFERENCES deals(id),
    user_id uuid NOT NULL REFERENCES users(id),
    calendar_id text NOT NULL DEFAULT 'primary',
    google_event_id text NOT NULL,
    etag text,
    last_synced_at timestamptz,
    last_direction text CHECK (last_direction IN ('to_google', 'from_google')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (activity_id),
    UNIQUE (user_id, calendar_id, google_event_id))""",
    # ------------------------------------------------------------------------ calendar_sync_state
    f"""CREATE TABLE calendar_sync_state (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    user_id uuid NOT NULL REFERENCES users(id),
    calendar_id text NOT NULL DEFAULT 'primary',
    sync_token text,
    channel_id text,
    channel_token text,
    resource_id text,
    channel_expiry timestamptz,
    last_full_sync_at timestamptz,
    last_push_at timestamptz,
    UNIQUE (user_id, calendar_id))""",
    "CREATE INDEX calendar_sync_state_channel_idx ON calendar_sync_state (channel_id)",
    # --------------------------------------------------------------------- gmail_logged_threads
    f"""CREATE TABLE gmail_logged_threads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    deal_id uuid NOT NULL REFERENCES deals(id),
    activity_id uuid NOT NULL REFERENCES activities(id),
    user_id uuid NOT NULL REFERENCES users(id),
    gmail_thread_id text NOT NULL,
    subject text,
    logged_at timestamptz NOT NULL DEFAULT now(),
    logged_by uuid NOT NULL REFERENCES users(id),
    deleted_at timestamptz,
    deleted_reason text,
    UNIQUE (user_id, gmail_thread_id))""",
    "CREATE INDEX gmail_logged_threads_deal_idx ON gmail_logged_threads (deal_id)",
    # -------------------------------------------------------------------- gmail_logged_messages
    f"""CREATE TABLE gmail_logged_messages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    thread_row_id uuid NOT NULL REFERENCES gmail_logged_threads(id),
    gmail_message_id text NOT NULL,
    user_id uuid NOT NULL REFERENCES users(id),
    from_email text NOT NULL,
    to_emails text[] NOT NULL DEFAULT '{{}}',
    cc_emails text[] NOT NULL DEFAULT '{{}}',
    sent_at timestamptz NOT NULL,
    body_text text,
    attachments jsonb NOT NULL DEFAULT '[]',
    UNIQUE (user_id, gmail_message_id))""",
    "CREATE INDEX gmail_logged_messages_thread_idx ON gmail_logged_messages (thread_row_id, sent_at)",
    # ------------------------------------------------------------------------- calendar_suggestions
    f"""CREATE TABLE calendar_suggestions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id {ORG},
    user_id uuid NOT NULL REFERENCES users(id),
    google_event_id text NOT NULL,
    calendar_id text NOT NULL DEFAULT 'primary',
    deal_id uuid REFERENCES deals(id),
    contact_email text,
    summary text,
    starts_at timestamptz,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'linked', 'ignored')),
    linked_activity_id uuid REFERENCES activities(id),
    decided_by uuid REFERENCES users(id),
    decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, google_event_id))""",
    "CREATE INDEX calendar_suggestions_open_idx ON calendar_suggestions (user_id, status)",
    # ----------------------------------------------------------------------- activities (Phase 3)
    """ALTER TABLE activities
    ADD COLUMN send_invite boolean NOT NULL DEFAULT false,
    ADD COLUMN add_meet_link boolean NOT NULL DEFAULT false,
    ADD COLUMN meet_link text""",
    # ------------------------------------------------------------------------------- grants
    """DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'relay_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON google_accounts, google_event_links,
        calendar_sync_state, gmail_logged_threads, gmail_logged_messages, calendar_suggestions
        TO relay_app;
    REVOKE DELETE ON gmail_logged_messages FROM relay_app;
    END IF; END $$""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(sa.text(stmt))


def downgrade() -> None:
    for table in (
        "calendar_suggestions", "gmail_logged_messages", "gmail_logged_threads",
        "calendar_sync_state", "google_event_links", "google_accounts",
    ):  # fmt: skip
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
    op.execute(
        sa.text(
            "ALTER TABLE activities "
            "DROP COLUMN IF EXISTS send_invite, "
            "DROP COLUMN IF EXISTS add_meet_link, "
            "DROP COLUMN IF EXISTS meet_link"
        )
    )
