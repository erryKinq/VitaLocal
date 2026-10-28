"""Habit gamification: an icon per habit, the template it was added from, and unlocked achievements
(kept once earned, with a seen flag for the "new badge" notice).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-02
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE habit ADD COLUMN icon text CHECK (icon IS NULL OR length(icon) BETWEEN 1 AND 16);
        ALTER TABLE habit ADD COLUMN template text CHECK (template IS NULL OR length(template) <= 40);

        CREATE TABLE habit_achievement (
            key          text PRIMARY KEY,
            habit_id     uuid REFERENCES habit ON DELETE CASCADE,  -- null: across all habits
            unlocked_on  date NOT NULL,
            seen         boolean NOT NULL DEFAULT false,
            created_at   timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE habit_achievement;
        ALTER TABLE habit DROP COLUMN template;
        ALTER TABLE habit DROP COLUMN icon;
    """)
