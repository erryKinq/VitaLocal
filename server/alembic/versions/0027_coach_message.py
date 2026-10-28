"""AI coach: one row per request (a workout debrief for now) with the facts sent, the validated
answer and its state while the local model works on it.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-24
"""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE coach_message (
            id             uuid PRIMARY KEY,
            kind           text NOT NULL CHECK (kind IN ('debrief')),
            activity_id    bigint REFERENCES activity ON DELETE CASCADE,
            lang           text NOT NULL DEFAULT 'de',
            status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'error')),
            facts          jsonb,
            result         jsonb,      -- the validated answer
            model          text,
            error          text,
            repaired       boolean NOT NULL DEFAULT false,  -- the first answer failed validation
            prompt_version int,
            duration_ms    int,
            created_at     timestamptz NOT NULL DEFAULT now(),
            finished_at    timestamptz
        );
        CREATE INDEX coach_message_activity ON coach_message (activity_id, created_at DESC);
        CREATE INDEX coach_message_created ON coach_message (created_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE coach_message")
