"""Exercises loaded with adjustable dumbbells: the progression steps to weights the plates allow.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-03
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0: steps of increment_kg; 1: the weight of one dumbbell; 2: the sum of two dumbbells
    op.execute(
        "ALTER TABLE exercise ADD COLUMN dumbbells smallint NOT NULL DEFAULT 0"
        " CHECK (dumbbells BETWEEN 0 AND 2)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE exercise DROP COLUMN dumbbells")
