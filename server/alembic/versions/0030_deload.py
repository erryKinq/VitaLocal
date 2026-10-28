"""Deload: a plan may plan one every N weeks; a deload week is a date range on the plan; workouts in it
are marked and left out of the progression.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("training_plan", sa.Column("deload_every_weeks", sa.Integer, nullable=True))
    op.create_check_constraint(
        "training_plan_deload_every_check", "training_plan", "deload_every_weeks BETWEEN 2 AND 16"
    )
    op.add_column("training_plan", sa.Column("deload_from", sa.Date, nullable=True))
    op.add_column("training_plan", sa.Column("deload_until", sa.Date, nullable=True))
    op.create_check_constraint(
        "training_plan_deload_range_check",
        "training_plan",
        "(deload_from IS NULL) = (deload_until IS NULL) AND (deload_from IS NULL OR deload_from <= deload_until)",
    )
    op.add_column(
        "training_session", sa.Column("deload", sa.Boolean, nullable=False, server_default=sa.false())
    )


def downgrade() -> None:
    op.drop_column("training_session", "deload")
    op.drop_constraint("training_plan_deload_range_check", "training_plan")
    op.drop_column("training_plan", "deload_until")
    op.drop_column("training_plan", "deload_from")
    op.drop_constraint("training_plan_deload_every_check", "training_plan")
    op.drop_column("training_plan", "deload_every_weeks")
