"""Time exercises get a range like the reps (30–45 s): at its top the next weight, back to the bottom.
`duration_s` stays the current target.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan_exercise", sa.Column("duration_min_s", sa.Integer, nullable=True))
    op.add_column("plan_exercise", sa.Column("duration_max_s", sa.Integer, nullable=True))
    op.create_check_constraint(
        "plan_exercise_duration_range_check",
        "plan_exercise",
        "duration_min_s BETWEEN 1 AND 3600 AND duration_max_s BETWEEN 1 AND 3600"
        " AND duration_min_s <= duration_max_s OR duration_min_s IS NULL OR duration_max_s IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("plan_exercise_duration_range_check", "plan_exercise")
    op.drop_column("plan_exercise", "duration_max_s")
    op.drop_column("plan_exercise", "duration_min_s")
