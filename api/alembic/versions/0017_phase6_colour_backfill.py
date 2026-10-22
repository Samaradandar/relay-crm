# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 6 (P6-03): backfill a colour for every team created before this phase (teams.colour has
existed as a column since 0001, but nothing ever populated it). Runs the same OKLCH generator
the regenerate endpoint uses, so backfilled colours satisfy the same distinctness/contrast rules.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa

from alembic import op
from app.modules.performance.colours import generate_colour

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    org_ids = [r[0] for r in conn.execute(sa.text("SELECT DISTINCT org_id FROM teams")).fetchall()]
    for org_id in org_ids:
        rows = conn.execute(
            sa.text(
                "SELECT id, colour FROM teams WHERE org_id = :o AND deleted_at IS NULL ORDER BY created_at"
            ).bindparams(o=org_id)
        ).fetchall()
        colours = [r[1] for r in rows if r[1]]
        for team_id, colour in rows:
            if colour:
                continue
            new_colour = generate_colour(colours)
            conn.execute(
                sa.text("UPDATE teams SET colour = :c WHERE id = :t").bindparams(
                    c=new_colour, t=team_id
                )
            )
            colours.append(new_colour)


def downgrade() -> None:
    pass  # leaving colours in place on downgrade is harmless and avoids losing manual changes
