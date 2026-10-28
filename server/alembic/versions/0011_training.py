"""Training: own exercise library, session layer (title, plan day, added/hidden sets, ratings),
manual workouts, plans with split days and prescribed exercises.

Everything here is the user's layer on top of imported activities: it is keyed by the activity
id (stable across re-imports: activities upsert on device + start) and by the FIT set index
(stable, `activity_set` is replaced wholesale on re-import). Sets added by hand live in their own
table so a re-import never touches them.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-30
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE exercise (
            id                uuid PRIMARY KEY,
            name              text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            garmin_category   text,              -- FIT exercise_category (matches watch sets)
            garmin_name       text,              -- FIT <category>_exercise_name
            kind              text NOT NULL DEFAULT 'reps' CHECK (kind IN ('reps', 'time')),
            muscles           text[] NOT NULL DEFAULT '{}',   -- primary: count 1 hard set each
            muscles_secondary text[] NOT NULL DEFAULT '{}',   -- secondary: count 0.5
            increment_kg      double precision NOT NULL DEFAULT 2.5
                              CHECK (increment_kg > 0 AND increment_kg <= 50),
            bodyweight        boolean NOT NULL DEFAULT false, -- weight is extra load (pull-ups, dips)
            note              text,
            archived          boolean NOT NULL DEFAULT false,
            created_at        timestamptz NOT NULL DEFAULT now(),
            CHECK (garmin_name IS NULL OR garmin_category IS NOT NULL)
        );
        CREATE UNIQUE INDEX exercise_name_key ON exercise (lower(name));

        CREATE TABLE training_plan (
            id         uuid PRIMARY KEY,
            name       text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            note       text,
            active     boolean NOT NULL DEFAULT false,  -- the plan whose rotation is suggested
            archived   boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            CHECK (NOT (active AND archived))
        );
        CREATE UNIQUE INDEX training_plan_one_active ON training_plan (active) WHERE active;

        CREATE TABLE plan_day (
            id       uuid PRIMARY KEY,
            plan_id  uuid NOT NULL REFERENCES training_plan ON DELETE CASCADE,
            name     text NOT NULL CHECK (length(name) BETWEEN 1 AND 60),   -- "Push", "A" …
            position int NOT NULL DEFAULT 0,                                -- rotation order
            note     text
        );
        CREATE INDEX plan_day_plan ON plan_day (plan_id, position);

        CREATE TABLE plan_exercise (
            id            uuid PRIMARY KEY,
            plan_day_id   uuid NOT NULL REFERENCES plan_day ON DELETE CASCADE,
            exercise_id   uuid NOT NULL REFERENCES exercise,
            position      int NOT NULL DEFAULT 0,
            sets          int NOT NULL DEFAULT 3 CHECK (sets BETWEEN 1 AND 20),
            reps_min      int CHECK (reps_min BETWEEN 1 AND 100),       -- rep range (kind reps)
            reps_max      int CHECK (reps_max BETWEEN 1 AND 100),
            duration_s    int CHECK (duration_s BETWEEN 1 AND 3600),    -- kind time
            rest_s        int CHECK (rest_s BETWEEN 0 AND 1800),
            -- current target; "Nächstes Mal" suggestions are written here when taken over
            target_reps   int CHECK (target_reps BETWEEN 1 AND 100),
            target_weight_kg double precision CHECK (target_weight_kg >= 0 AND target_weight_kg <= 1000),
            note          text,
            CHECK (reps_min IS NULL OR reps_max IS NULL OR reps_min <= reps_max)
        );
        CREATE INDEX plan_exercise_day ON plan_exercise (plan_day_id, position);

        -- The user's layer on an activity (imported or manual).
        CREATE TABLE training_session (
            activity_id  bigint PRIMARY KEY REFERENCES activity ON DELETE CASCADE,
            title        text CHECK (length(title) <= 80),
            note         text,
            plan_day_id  uuid REFERENCES plan_day ON DELETE SET NULL,
            reviewed_at  timestamptz,       -- sets and plan day confirmed; only then used for progression
            updated_at   timestamptz NOT NULL DEFAULT now()
        );

        -- Watch sets: which exercise, hidden (junk) or warm-up; values stay in the existing edit table.
        ALTER TABLE activity_set_edit
            ADD COLUMN exercise_id uuid REFERENCES exercise ON DELETE SET NULL,
            -- which entry of the plan day (the same exercise can appear twice, e.g. heavy and light)
            ADD COLUMN plan_exercise_id uuid REFERENCES plan_exercise ON DELETE SET NULL,
            ADD COLUMN hidden      boolean NOT NULL DEFAULT false,
            ADD COLUMN warmup      boolean NOT NULL DEFAULT false,
            ADD COLUMN duration_s  double precision CHECK (duration_s >= 0);

        -- Sets entered by hand (missing on the watch, or a manual workout).
        CREATE TABLE training_set (
            id          uuid PRIMARY KEY,
            activity_id bigint NOT NULL REFERENCES activity ON DELETE CASCADE,
            position    double precision NOT NULL,   -- ordering among watch sets (set_index + fraction)
            exercise_id uuid REFERENCES exercise ON DELETE SET NULL,
            plan_exercise_id uuid REFERENCES plan_exercise ON DELETE SET NULL,
            repetitions int CHECK (repetitions BETWEEN 0 AND 1000),
            weight_kg   double precision CHECK (weight_kg >= 0 AND weight_kg <= 1000),
            duration_s  double precision CHECK (duration_s >= 0),
            warmup      boolean NOT NULL DEFAULT false,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX training_set_activity ON training_set (activity_id, position);

        -- per exercise of a session, or per plan entry when the day has the exercise more than once
        CREATE TABLE exercise_rating (
            activity_id      bigint NOT NULL REFERENCES activity ON DELETE CASCADE,
            exercise_id      uuid NOT NULL REFERENCES exercise ON DELETE CASCADE,
            plan_exercise_id uuid REFERENCES plan_exercise ON DELETE CASCADE,
            rating           text NOT NULL CHECK (rating IN ('easy', 'good', 'hard')),
            updated_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX exercise_rating_key ON exercise_rating
            (activity_id, exercise_id, COALESCE(plan_exercise_id, '00000000-0000-0000-0000-000000000000'));

        -- Manual workouts: an activity without device and import.
        ALTER TABLE activity ALTER COLUMN device_id DROP NOT NULL;
        ALTER TABLE activity ADD COLUMN manual boolean NOT NULL DEFAULT false;
        ALTER TABLE activity ADD COLUMN manual_key uuid UNIQUE;  -- client id: POST is safe to repeat
        ALTER TABLE activity DROP CONSTRAINT activity_has_source;
        ALTER TABLE activity ADD CONSTRAINT activity_has_source
            CHECK (gb_parse_run_id IS NOT NULL OR fit_parse_run_id IS NOT NULL OR manual);
        ALTER TABLE activity ADD CONSTRAINT activity_manual_no_device CHECK (NOT manual OR device_id IS NULL);
        ALTER TABLE activity ADD CONSTRAINT activity_device_unless_manual
            CHECK (manual OR device_id IS NOT NULL);
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM activity WHERE manual;
        ALTER TABLE activity DROP CONSTRAINT activity_device_unless_manual;
        ALTER TABLE activity DROP CONSTRAINT activity_manual_no_device;
        ALTER TABLE activity DROP CONSTRAINT activity_has_source;
        ALTER TABLE activity ADD CONSTRAINT activity_has_source
            CHECK (gb_parse_run_id IS NOT NULL OR fit_parse_run_id IS NOT NULL);
        ALTER TABLE activity DROP COLUMN manual_key, DROP COLUMN manual;
        ALTER TABLE activity ALTER COLUMN device_id SET NOT NULL;
        DROP TABLE exercise_rating; DROP TABLE training_set;
        ALTER TABLE activity_set_edit DROP COLUMN plan_exercise_id, DROP COLUMN exercise_id,
            DROP COLUMN hidden, DROP COLUMN warmup, DROP COLUMN duration_s;
        DROP TABLE training_session; DROP TABLE plan_exercise; DROP TABLE plan_day;
        DROP TABLE training_plan; DROP TABLE exercise;
    """)
