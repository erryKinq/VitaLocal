"""Finer muscles: chest, shoulders and back are split into their parts (upper and mid/lower chest; front,
side and rear delts, rotator cuff; lats, upper back), traps are the upper traps, the neck is a muscle.
Every exercise gets the parts it trains (`muscle_refine`), so past workouts count them too; the coach's
focus muscles stand for their parts.

Uses the app's `muscle_refine` and library on purpose: an instance that upgrades later gets the
knowledge of that version.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-25
"""

import json

import sqlalchemy as sa
from alembic import op

from vitalocal import muscle_refine, training

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def _rewrite(fn) -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, name, garmin_category, garmin_name, library_key, muscles, muscles_secondary "
            "FROM exercise"
        )
    ).all()
    for r in rows:
        prim, sec = fn(dict(r._mapping))
        if (prim, sec) != (list(r.muscles), list(r.muscles_secondary)):
            conn.execute(
                sa.text("UPDATE exercise SET muscles = :p, muscles_secondary = :s WHERE id = :id"),
                {"p": prim, "s": sec, "id": r.id},
            )


def _focus(fn) -> None:
    conn = op.get_bind()
    answers = conn.execute(sa.text("SELECT answers FROM coach_profile WHERE id = 1")).scalar()
    if not answers or not answers.get("focus"):
        return
    conn.execute(
        sa.text("UPDATE coach_profile SET answers = CAST(:a AS jsonb) WHERE id = 1"),
        {"a": json.dumps({**answers, "focus": fn(answers["focus"])}, ensure_ascii=False)},
    )


def upgrade() -> None:
    _rewrite(muscle_refine.refine)
    _focus(lambda f: training.expand(f)[0])


def downgrade() -> None:
    _rewrite(lambda ex: muscle_refine.coarsen(ex["muscles"], ex["muscles_secondary"]))
    _focus(lambda f: muscle_refine.coarsen(f, [])[0])
