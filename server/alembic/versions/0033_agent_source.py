"""Writes by an AI agent are marked `agent`: the earlier name for it in `source` and `entered_via`
becomes `agent` in every table that has such a column.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

OLD = "hermes"


def upgrade() -> None:
    conn = op.get_bind()
    cols = conn.execute(
        sa.text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND column_name IN ('source', 'entered_via') "
            "AND data_type = 'text' "
            "AND table_name IN (SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE')"
        )
    ).all()
    for table, column in cols:
        conn.execute(sa.text(f'UPDATE "{table}" SET "{column}" = \'agent\' WHERE "{column}" = :old'), {"old": OLD})


def downgrade() -> None:
    # which `agent` rows were written under the old name is not recorded; they stay `agent`
    pass
