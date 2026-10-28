"""Raw store retention: remember which artifacts' bytes were pruned.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE raw_artifact ADD COLUMN pruned_at timestamptz;
        ALTER TABLE ingest_batch ADD COLUMN raw_pruned_at timestamptz;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE ingest_batch DROP COLUMN raw_pruned_at;
        ALTER TABLE raw_artifact DROP COLUMN pruned_at;
    """)
