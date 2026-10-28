"""Index for nested archive members: deleting a batch's members (reprocess) checked every member
row for children without it.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-03
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX artifact_member_nested_in_idx ON artifact_member (nested_in)"
        " WHERE nested_in IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX artifact_member_nested_in_idx")
