"""Supplements taken on some days of the week only (e.g. vitamin D once a week).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-02
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE supplement ADD COLUMN per_week int NOT NULL DEFAULT 7 CHECK (per_week BETWEEN 1 AND 7)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE supplement DROP COLUMN per_week")
