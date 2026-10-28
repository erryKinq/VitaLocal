"""Drop Garmin readiness factors stored as 0 by the FIT parser 1.4.0: the watch writes 0 (with
level 0) for a factor that is not available yet; parser 1.4.1 skips them.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-04
"""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM sample WHERE metric LIKE 'garmin.readiness\\_factor.%' AND value = 0")


def downgrade() -> None:
    pass
