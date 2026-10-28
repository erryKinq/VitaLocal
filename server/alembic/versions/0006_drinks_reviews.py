"""Drinks leave the check-ins; journal versions, revisions, weekly/monthly reviews and search.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        -- Drinks are nutrition, not journal: own table, existing water check-ins move over (same id,
        -- so clients that still hold those ids can delete them).
        CREATE TABLE drink (
            id         uuid PRIMARY KEY,
            ts         timestamptz NOT NULL,
            drink      text NOT NULL,
            ml         double precision NOT NULL CHECK (ml > 0 AND ml <= 5000),
            source     text NOT NULL DEFAULT 'webui',
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX drink_ts_idx ON drink (ts);
        INSERT INTO drink (id, ts, drink, ml, source, created_at)
            SELECT id, ts, coalesce(label, 'water'), value, source, created_at
            FROM checkin WHERE kind = 'water' AND value > 0;
        DELETE FROM checkin WHERE kind = 'water';
        ALTER TABLE checkin DROP CONSTRAINT checkin_kind_check;
        ALTER TABLE checkin ADD CONSTRAINT checkin_kind_check
            CHECK (kind IN ('mood', 'energy', 'symptom', 'rpe', 'note'));
        UPDATE habit SET metric = 'nutrition.drinks' WHERE metric = 'checkin.water';

        -- Optimistic locking: every write bumps `version`; an empty text keeps the row (version
        -- history must not restart, or a stale client could match a recreated version).
        ALTER TABLE journal_day
            ADD COLUMN version int NOT NULL DEFAULT 1,
            ADD COLUMN source  text NOT NULL DEFAULT 'webui';

        -- Weekly (Monday..Sunday) and monthly reviews. `facts` is the aggregate snapshot taken at
        -- the last write, so a review text can be checked against the numbers it was written on.
        CREATE TABLE journal_review (
            scope        text NOT NULL CHECK (scope IN ('week', 'month')),
            period_start date NOT NULL,
            period_end   date NOT NULL,
            text         text NOT NULL,
            version      int NOT NULL DEFAULT 1,
            source       text NOT NULL DEFAULT 'webui',
            facts        jsonb,
            updated_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (scope, period_start)
        );

        -- Append-only: the text as it was before being replaced (see api/journal.py for when).
        CREATE TABLE journal_revision (
            id         bigserial PRIMARY KEY,
            scope      text NOT NULL CHECK (scope IN ('day', 'week', 'month')),
            period     date NOT NULL,
            version    int NOT NULL,
            text       text NOT NULL,
            source     text NOT NULL,
            written_at timestamptz NOT NULL,
            saved_at   timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX journal_revision_lookup_idx ON journal_revision (scope, period, id DESC);

        -- Full-text search; the journal is written in German, so German stemming.
        CREATE INDEX journal_day_fts_idx ON journal_day USING gin (to_tsvector('german', text));
        CREATE INDEX journal_review_fts_idx ON journal_review USING gin (to_tsvector('german', text));
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE journal_revision; DROP TABLE journal_review;
        DROP INDEX journal_day_fts_idx;
        DELETE FROM journal_day WHERE text = '';
        ALTER TABLE journal_day DROP COLUMN version, DROP COLUMN source;
        UPDATE habit SET metric = 'checkin.water' WHERE metric = 'nutrition.drinks';
        ALTER TABLE checkin DROP CONSTRAINT checkin_kind_check;
        ALTER TABLE checkin ADD CONSTRAINT checkin_kind_check
            CHECK (kind IN ('mood', 'energy', 'water', 'symptom', 'rpe', 'note'));
        INSERT INTO checkin (id, ts, kind, value, label, source, created_at)
            SELECT id, ts, 'water', ml, drink, source, created_at FROM drink;
        DROP TABLE drink;
    """)
