"""Exercises get the equipment they need and, when taken over from the built-in library, its key
(an exercise links to at most one library entry).

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "exercise",
        sa.Column("equipment", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
    )
    op.add_column("exercise", sa.Column("library_key", sa.Text, nullable=True))
    op.create_unique_constraint("exercise_library_key_key", "exercise", ["library_key"])


def downgrade() -> None:
    op.drop_constraint("exercise_library_key_key", "exercise")
    op.drop_column("exercise", "library_key")
    op.drop_column("exercise", "equipment")
