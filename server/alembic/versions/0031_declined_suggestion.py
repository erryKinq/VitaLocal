"""A declined progression suggestion: the plan entry keeps its target, and this suggestion is not
offered again (a different one is).

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plan_exercise", sa.Column("declined_suggestion", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("plan_exercise", "declined_suggestion")
