"""Gadgetbridge generic metrics with verified meaning get their own names (parser 1.3).

Garmin's training readiness (type 8, the extra is its level), VO2max (12) and the remaining
recovery time (23, minutes) were stored as `gb.metric.<type>`.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-01
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

NAMES = {
    "gb.metric.8": ("garmin.training_readiness", "score"),
    "gb.metric.8.extra": ("garmin.training_readiness_level", None),
    "gb.metric.12": ("vo2max", "ml/kg/min"),
    "gb.metric.23": ("recovery_time", "min"),
}


def _rename(pairs) -> None:
    for old, (new, unit) in pairs:
        # a re-import under the new parser may already have stored the new name for some rows
        op.execute(f"""
            DELETE FROM sample o USING sample n
            WHERE o.metric = '{old}' AND n.metric = '{new}' AND n.device_id = o.device_id AND n.ts = o.ts
        """)
        u = f"'{unit}'" if unit else "NULL"
        op.execute(f"UPDATE sample SET metric = '{new}', unit = {u} WHERE metric = '{old}'")


def upgrade() -> None:
    _rename(NAMES.items())


def downgrade() -> None:
    _rename((new, (old, None)) for old, (new, _) in NAMES.items())
