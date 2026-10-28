"""FIT activity normalization: session fields on activity, activity_set table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-28
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        -- An activity can now originate from the Gadgetbridge DB summary, a FIT session, or both.
        ALTER TABLE activity RENAME COLUMN parse_run_id TO gb_parse_run_id;
        ALTER TABLE activity ALTER COLUMN gb_parse_run_id DROP NOT NULL;
        ALTER TABLE activity
            ADD COLUMN fit_parse_run_id   bigint REFERENCES parse_run,
            ADD COLUMN fit_session_index  int,
            ADD COLUMN sport              text,
            ADD COLUMN sub_sport          text,
            ADD COLUMN sport_profile_name text,
            ADD COLUMN elapsed_s          double precision,
            ADD COLUMN timer_s            double precision,
            ADD COLUMN distance_m         double precision,
            ADD COLUMN energy_kcal        double precision,
            ADD COLUMN avg_hr             int,
            ADD COLUMN max_hr             int,
            ADD COLUMN fit_attrs          jsonb,
            ADD CONSTRAINT activity_has_source
                CHECK (gb_parse_run_id IS NOT NULL OR fit_parse_run_id IS NOT NULL);

        CREATE TABLE activity_set (
            activity_id          bigint NOT NULL REFERENCES activity ON DELETE CASCADE,
            set_index            int NOT NULL,          -- FIT message_index
            set_type             text NOT NULL,          -- active | rest
            start_ts             timestamptz,
            duration_s           double precision,
            repetitions          int,
            weight_kg            double precision,
            exercise_category    text,                   -- FIT exercise_category of first candidate
            category_candidates  int[],                  -- raw candidates as recorded by the watch
            subtype_candidates   int[],
            wkt_step_index       int,
            parse_run_id         bigint NOT NULL REFERENCES parse_run,
            PRIMARY KEY (activity_id, set_index)
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE activity_set;
        DELETE FROM activity WHERE gb_parse_run_id IS NULL;
        ALTER TABLE activity
            DROP CONSTRAINT activity_has_source,
            DROP COLUMN fit_parse_run_id, DROP COLUMN fit_session_index, DROP COLUMN sport,
            DROP COLUMN sub_sport, DROP COLUMN sport_profile_name, DROP COLUMN elapsed_s,
            DROP COLUMN timer_s, DROP COLUMN distance_m, DROP COLUMN energy_kcal,
            DROP COLUMN avg_hr, DROP COLUMN max_hr, DROP COLUMN fit_attrs;
        ALTER TABLE activity ALTER COLUMN gb_parse_run_id SET NOT NULL;
        ALTER TABLE activity RENAME COLUMN gb_parse_run_id TO parse_run_id;
    """)
