"""Habits (manual or derived from a daily metric), app settings and cached AI summaries.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE habit (
            id            uuid PRIMARY KEY,
            name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            -- check: done / not done; count: a number per day compared against `target`
            kind          text NOT NULL CHECK (kind IN ('check', 'count')),
            unit          text,
            target        double precision,             -- per day (count) — null for check
            per_week      int NOT NULL DEFAULT 7 CHECK (per_week BETWEEN 1 AND 7),
            -- optional automatic evaluation from a daily metric (e.g. steps >= 8000)
            metric        text,
            comparator    text CHECK (comparator IN ('>=', '<=')),
            threshold     double precision,
            position      int NOT NULL DEFAULT 0,
            archived      boolean NOT NULL DEFAULT false,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CHECK ((metric IS NULL) = (comparator IS NULL) AND (metric IS NULL) = (threshold IS NULL))
        );

        CREATE TABLE habit_log (
            habit_id   uuid NOT NULL REFERENCES habit ON DELETE CASCADE,
            day        date NOT NULL,
            value      double precision NOT NULL,   -- 1 = done for check habits
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (habit_id, day)
        );

        CREATE TABLE app_setting (
            key        text PRIMARY KEY,
            value      jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        -- Generated summaries with the exact input they were based on (traceability).
        CREATE TABLE ai_summary (
            id           bigserial PRIMARY KEY,
            scope        text NOT NULL,        -- 'day' | 'week'
            day          date NOT NULL,        -- the day, or the last day of the week
            lang         text NOT NULL,
            model        text NOT NULL,
            context      jsonb NOT NULL,
            text         text NOT NULL,
            duration_ms  int,
            created_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ai_summary_lookup_idx ON ai_summary (scope, day, lang, created_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE ai_summary; DROP TABLE app_setting; DROP TABLE habit_log; DROP TABLE habit;")
