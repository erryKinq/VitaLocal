"""More Gadgetbridge generic metrics get their own names (parser 1.4): the 24/7 VO2max and the
race predictions (5 km, 10 km, half marathon, marathon).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-01
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

NAMES = {
    "gb.metric.6": ("vo2max_daily", "ml/kg/min"),
    "gb.metric.19": ("race_time_5k", "s"),
    "gb.metric.20": ("race_time_10k", "s"),
    "gb.metric.21": ("race_time_half_marathon", "s"),
    "gb.metric.22": ("race_time_marathon", "s"),
    "gb.metric.24": ("daily.total_ascent", "m"),
    "gb.metric.25": ("daily.total_descent", "m"),
}


def _rename(pairs) -> None:
    for old, (new, unit) in pairs:
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
