"""Daily goals per metric (nutrition), versioned so every day is judged by the goal valid then.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-29
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        -- One row per goal version. The version valid on a day is the one with the latest
        -- valid_from on or before it; valid_from NULL = since always (the first goal known).
        -- Both bounds NULL: the goal was removed from then on.
        CREATE TABLE goal (
            id          bigserial PRIMARY KEY,
            metric      text NOT NULL,
            min_value   double precision,
            max_value   double precision,
            valid_from  date,
            source      text NOT NULL CHECK (source IN ('waistline', 'vitalocal')),
            created_at  timestamptz NOT NULL DEFAULT now(),
            CHECK (min_value IS NULL OR max_value IS NULL OR min_value <= max_value)
        );
        CREATE UNIQUE INDEX goal_version_idx ON goal (metric, coalesce(valid_from, DATE '0001-01-01'));
    """)


def downgrade() -> None:
    op.execute("DROP TABLE goal;")
