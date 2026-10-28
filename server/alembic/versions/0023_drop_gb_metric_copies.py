"""Drop Gadgetbridge GENERIC_METRIC 9/10/11: they repeat the training load (acute, chronic) and the
resting metabolic rate of their own tables value for value (checked in prod on all 1324 samples).
The Gadgetbridge parser 1.5 no longer stores them.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-04
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM sample WHERE metric IN ('gb.metric.9', 'gb.metric.10', 'gb.metric.11')")


def downgrade() -> None:
    pass  # the copies come back with a reprocess by an older parser
