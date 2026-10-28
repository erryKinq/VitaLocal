"""Lab results: reports with their values, personal targets, custom analytes and synonyms, imports.

The analyte catalog lives in code (vitalocal/labs.py); `lab_analyte` only holds analytes added by
the user and `lab_synonym` the name mappings confirmed in the review of an import. A report's
original file is a raw artifact outside any ingest batch, so snapshot retention never removes it.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-29
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE lab_analyte (
            key         text PRIMARY KEY CHECK (key ~ '^x_[a-z0-9_]{1,40}$'),
            label       text NOT NULL CHECK (length(label) BETWEEN 1 AND 80),
            grp         text NOT NULL DEFAULT 'other',
            unit        text NOT NULL,
            material    text CHECK (material IN ('serum', 'plasma', 'whole_blood', 'urine')),
            molar_mass  double precision CHECK (molar_mass > 0),
            created_at  timestamptz NOT NULL DEFAULT now()
        );

        -- normalized printed name (+ material, '' = any) -> analyte (catalog key or lab_analyte.key)
        CREATE TABLE lab_synonym (
            name_norm   text NOT NULL,
            material    text NOT NULL DEFAULT '',
            analyte_key text NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (name_norm, material)
        );

        CREATE TABLE lab_report (
            id              uuid PRIMARY KEY,
            collected_on    date NOT NULL,           -- sample taken (Abnahme); the date of every value
            reported_on     date,                    -- report date as printed
            lab             text,
            note            text,
            artifact_sha256 char(64) REFERENCES raw_artifact,
            filename        text,
            source          text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'import')),
            import_meta     jsonb,                   -- model, duration, what the model proposed
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX lab_report_collected ON lab_report (collected_on);

        CREATE TABLE lab_value (
            id          uuid PRIMARY KEY,
            report_id   uuid NOT NULL REFERENCES lab_report ON DELETE CASCADE,
            position    int NOT NULL DEFAULT 0,
            analyte_key text,                        -- null: not in the catalog, compared by name only
            name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),  -- as printed
            material    text,                        -- as printed, e.g. "EDTA-Blut"
            method      text,
            comparator  text CHECK (comparator IN ('<', '<=', '>', '>=')),
            value       numeric,                     -- scale as printed: 2.90 stays 2.90
            value_text  text,                        -- qualitative results, e.g. "negativ"
            unit        text,
            ref_text    text,                        -- reference range as printed
            ref_low     numeric,
            ref_high    numeric,
            flag        text,                        -- flag as printed (H, L, +, ↑ …)
            note        text,                        -- the lab's comment on this value
            CHECK (value IS NOT NULL OR value_text IS NOT NULL),
            CHECK (comparator IS NULL OR value IS NOT NULL),
            CHECK (ref_low IS NULL OR ref_high IS NULL OR ref_low <= ref_high)
        );
        CREATE INDEX lab_value_report ON lab_value (report_id);
        CREATE INDEX lab_value_analyte ON lab_value (analyte_key);

        CREATE TABLE lab_target (
            analyte_key text PRIMARY KEY,
            min_value   numeric,
            max_value   numeric,
            unit        text NOT NULL,
            note        text,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            CHECK (min_value IS NOT NULL OR max_value IS NOT NULL),
            CHECK (min_value IS NULL OR max_value IS NULL OR min_value <= max_value)
        );

        -- An uploaded report the local model reads; nothing becomes a lab_report before the review.
        CREATE TABLE lab_import (
            id              uuid PRIMARY KEY,
            artifact_sha256 char(64) NOT NULL REFERENCES raw_artifact,
            filename        text,
            media_type      text,
            status          text NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'running', 'ready', 'failed', 'confirmed')),
            pages           int,
            proposal        jsonb,
            error           text,
            model           text,
            duration_ms     int,
            client_meta     jsonb,
            report_id       uuid REFERENCES lab_report ON DELETE SET NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            started_at      timestamptz,
            finished_at     timestamptz
        );

        -- When a supplement was started, for the time before it was logged in VitaLocal.
        ALTER TABLE supplement ADD COLUMN started_on date;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE supplement DROP COLUMN started_on;
        DROP TABLE lab_import; DROP TABLE lab_target; DROP TABLE lab_value; DROP TABLE lab_report;
        DROP TABLE lab_synonym; DROP TABLE lab_analyte;
    """)
