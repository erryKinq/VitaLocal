"""Near-live ingest: a serial worker, snapshot ordering and incremental Gadgetbridge parsing.

- `superseded`: a pending live snapshot that a newer one of the same source replaced before it
  was processed (each Gadgetbridge database is a full snapshot, so nothing is lost).
- `as_of`: when the snapshot was taken (export time), so an old export uploaded late never
  overwrites newer data. `force_reparse`: a queued reprocess parses known files again.
- `gb_snapshot_state`: the Gadgetbridge database snapshot applied last, with the newest
  TIMESTAMP per table (raw units). The next snapshot is parsed from there minus an overlap.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-03
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE ingest_batch DROP CONSTRAINT ingest_batch_status_check;
        ALTER TABLE ingest_batch ADD CONSTRAINT ingest_batch_status_check CHECK (status IN
            ('pending', 'processing', 'completed', 'completed_with_errors', 'failed', 'superseded'));
        ALTER TABLE ingest_batch
            ADD COLUMN as_of timestamptz,
            ADD COLUMN force_reparse boolean NOT NULL DEFAULT false;
        UPDATE ingest_batch SET as_of = CASE
            WHEN client_meta->>'exported_at' ~ '^\\d{4}-\\d{2}-\\d{2}T'
            THEN (client_meta->>'exported_at')::timestamptz ELSE received_at END;

        CREATE INDEX ingest_batch_source_received_idx ON ingest_batch (source, received_at DESC, id DESC);
        CREATE INDEX ingest_batch_open_idx ON ingest_batch (id) WHERE status IN ('pending', 'processing');
        CREATE INDEX ingest_batch_finished_idx ON ingest_batch (finished_at DESC);
        CREATE INDEX parse_run_done_idx ON parse_run (artifact_sha256, parser_name, parser_version)
            WHERE status = 'completed';
        -- an activity finds its FIT file by name in any export (the live database has none)
        CREATE INDEX artifact_member_basename_idx ON artifact_member ((regexp_replace(archive_path, '^.*/', '')));

        CREATE TABLE gb_snapshot_state (
            lineage         text PRIMARY KEY DEFAULT 'gadgetbridge',
            batch_id        bigint REFERENCES ingest_batch ON DELETE SET NULL,
            artifact_sha256 char(64) NOT NULL REFERENCES raw_artifact,
            as_of           timestamptz NOT NULL,
            parser_version  text NOT NULL,
            schema_version  int NOT NULL,
            table_hwm       jsonb NOT NULL,
            last_full_at    timestamptz,
            updated_at      timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE gb_snapshot_state;
        DROP INDEX artifact_member_basename_idx;
        DROP INDEX parse_run_done_idx;
        DROP INDEX ingest_batch_finished_idx;
        DROP INDEX ingest_batch_open_idx;
        DROP INDEX ingest_batch_source_received_idx;
        UPDATE ingest_batch SET status = 'completed' WHERE status = 'superseded';
        ALTER TABLE ingest_batch DROP COLUMN force_reparse, DROP COLUMN as_of;
        ALTER TABLE ingest_batch DROP CONSTRAINT ingest_batch_status_check;
        ALTER TABLE ingest_batch ADD CONSTRAINT ingest_batch_status_check CHECK (status IN
            ('pending', 'processing', 'completed', 'completed_with_errors', 'failed'));
    """)
