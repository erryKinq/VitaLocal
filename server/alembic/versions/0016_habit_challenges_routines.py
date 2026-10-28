"""Habit gamification, part two: hard habits (double flames from a day on), weekly challenges and
routines (habits done one after another, with bonus flames when the whole routine is done).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-02
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        -- a hard habit earns two flames per done day from this day on
        ALTER TABLE habit ADD COLUMN hard_since date;

        CREATE TABLE habit_routine (
            id          uuid PRIMARY KEY,
            name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            icon        text CHECK (icon IS NULL OR length(icon) BETWEEN 1 AND 16),
            habit_ids   uuid[] NOT NULL CHECK (cardinality(habit_ids) BETWEEN 2 AND 10),
            position    int NOT NULL DEFAULT 0,
            archived    boolean NOT NULL DEFAULT false,
            created_at  timestamptz NOT NULL DEFAULT now()
        );

        -- generated on the first look at a week; progress is live, completion is kept
        CREATE TABLE habit_challenge (
            week          date NOT NULL,              -- Monday
            key           text NOT NULL,
            kind          text NOT NULL CHECK (kind IN ('habit', 'perfect', 'flames', 'routine')),
            habit_id      uuid REFERENCES habit ON DELETE CASCADE,
            routine_id    uuid REFERENCES habit_routine ON DELETE CASCADE,
            target        int NOT NULL CHECK (target > 0),
            bonus         int NOT NULL CHECK (bonus >= 0),
            completed_on  date,
            PRIMARY KEY (week, key)
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE habit_challenge;
        DROP TABLE habit_routine;
        ALTER TABLE habit DROP COLUMN hard_since;
    """)
