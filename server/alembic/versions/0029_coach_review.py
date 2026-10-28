"""AI coach reviews the active plan: proposals of kind "review" (changes the person takes over one
by one, with the plan as it was kept for undo in the result).

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-24
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE coach_message DROP CONSTRAINT coach_message_kind_check;
        ALTER TABLE coach_message ADD CONSTRAINT coach_message_kind_check
            CHECK (kind IN ('debrief', 'plan', 'review'));
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM coach_message WHERE kind = 'review';
        ALTER TABLE coach_message DROP CONSTRAINT coach_message_kind_check;
        ALTER TABLE coach_message ADD CONSTRAINT coach_message_kind_check CHECK (kind IN ('debrief', 'plan'));
    """)
