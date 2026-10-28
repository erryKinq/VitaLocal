"""Skipped plan days: the rotation goes on after a skip, which stays on record in the history.
A plan entry's taken-over target also keeps its number of sets (the volume variant has one more).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-01
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE plan_day_skip (
            id          uuid PRIMARY KEY,
            plan_day_id uuid NOT NULL REFERENCES plan_day ON DELETE CASCADE,
            skipped_at  timestamptz NOT NULL DEFAULT now(),
            note        text CHECK (note IS NULL OR length(note) <= 500)
        );
        CREATE INDEX plan_day_skip_ts ON plan_day_skip (skipped_at);
        ALTER TABLE plan_exercise ADD COLUMN target_sets integer CHECK (target_sets BETWEEN 1 AND 20);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE plan_day_skip; ALTER TABLE plan_exercise DROP COLUMN target_sets")
