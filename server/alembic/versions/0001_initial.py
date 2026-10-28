"""Initial schema: raw store index, ingest provenance, devices, samples, activities.

Revision ID: 0001
Revises:
Create Date: 2026-09-28
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE raw_artifact (
            sha256        char(64) PRIMARY KEY,
            size          bigint NOT NULL,
            media_type    text,
            storage_path  text NOT NULL,
            first_seen_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE ingest_batch (
            id                    bigserial PRIMARY KEY,
            source                text NOT NULL,
            outer_artifact_sha256 char(64) NOT NULL REFERENCES raw_artifact,
            received_at           timestamptz NOT NULL DEFAULT now(),
            finished_at           timestamptz,
            status                text NOT NULL CHECK (status IN
                ('pending', 'processing', 'completed', 'completed_with_errors', 'failed')),
            client_meta           jsonb NOT NULL DEFAULT '{}',
            stats                 jsonb,
            error                 text,
            UNIQUE (source, outer_artifact_sha256)
        );

        CREATE TABLE device (
            id         serial PRIMARY KEY,
            source     text NOT NULL,
            source_key text NOT NULL,
            vendor     text,
            model      text,
            name       text,
            UNIQUE (source, source_key)
        );

        CREATE TABLE artifact_member (
            id              bigserial PRIMARY KEY,
            batch_id        bigint NOT NULL REFERENCES ingest_batch ON DELETE CASCADE,
            artifact_sha256 char(64) NOT NULL REFERENCES raw_artifact,
            archive_path    text NOT NULL,
            category        text NOT NULL,
            device_id       int REFERENCES device,
            nested_in       bigint REFERENCES artifact_member ON DELETE CASCADE,
            meta            jsonb NOT NULL DEFAULT '{}',
            UNIQUE (batch_id, archive_path)
        );
        CREATE INDEX artifact_member_category_idx ON artifact_member (category);
        CREATE INDEX artifact_member_sha_idx ON artifact_member (artifact_sha256);

        CREATE TABLE parse_run (
            id              bigserial PRIMARY KEY,
            batch_id        bigint REFERENCES ingest_batch ON DELETE SET NULL,
            artifact_sha256 char(64) NOT NULL REFERENCES raw_artifact,
            parser_name     text NOT NULL,
            parser_version  text NOT NULL,
            started_at      timestamptz NOT NULL DEFAULT now(),
            finished_at     timestamptz,
            status          text NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
            stats           jsonb,
            error           text
        );

        -- One row per measured value. Absent measurements are absent rows, never NULL/0.
        CREATE TABLE sample (
            ts           timestamptz NOT NULL,
            device_id    int NOT NULL REFERENCES device,
            metric       text NOT NULL,
            value        double precision NOT NULL,
            unit         text,
            parse_run_id bigint NOT NULL REFERENCES parse_run,
            PRIMARY KEY (metric, device_id, ts)
        );

        CREATE TABLE activity (
            id               bigserial PRIMARY KEY,
            device_id        int NOT NULL REFERENCES device,
            start_ts         timestamptz NOT NULL,
            end_ts           timestamptz,
            name             text,
            kind_raw         bigint,
            summary          jsonb,
            raw_details_name text,
            raw_fit_sha256   char(64) REFERENCES raw_artifact,
            has_gpx          boolean NOT NULL DEFAULT false,
            parse_run_id     bigint NOT NULL REFERENCES parse_run,
            UNIQUE (device_id, start_ts)
        );

        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
                CREATE EXTENSION IF NOT EXISTS timescaledb;
                PERFORM create_hypertable('sample', 'ts', chunk_time_interval => INTERVAL '7 days');
            ELSE
                RAISE NOTICE 'timescaledb not available: sample stays a plain table';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE activity; DROP TABLE sample; DROP TABLE parse_run;
        DROP TABLE artifact_member; DROP TABLE device; DROP TABLE ingest_batch;
        DROP TABLE raw_artifact;
    """)
