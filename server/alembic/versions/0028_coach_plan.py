"""AI coach builds plans: the questionnaire's answers (one profile), plan proposals as coach
messages, and the plan a proposal became.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-24
"""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE coach_profile (
            id         int PRIMARY KEY CHECK (id = 1),   -- one person per instance
            answers    jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE coach_message DROP CONSTRAINT coach_message_kind_check;
        ALTER TABLE coach_message ADD CONSTRAINT coach_message_kind_check CHECK (kind IN ('debrief', 'plan'));
        ALTER TABLE coach_message
            ADD COLUMN refines uuid REFERENCES coach_message ON DELETE SET NULL,  -- the proposal it revises
            ADD COLUMN applied_plan_id uuid REFERENCES training_plan ON DELETE SET NULL;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM coach_message WHERE kind = 'plan';
        ALTER TABLE coach_message DROP COLUMN applied_plan_id, DROP COLUMN refines;
        ALTER TABLE coach_message DROP CONSTRAINT coach_message_kind_check;
        ALTER TABLE coach_message ADD CONSTRAINT coach_message_kind_check CHECK (kind IN ('debrief'));
        DROP TABLE coach_profile;
    """)
