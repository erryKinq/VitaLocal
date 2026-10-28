"""Automatic habits take a range (min and/or max) instead of one comparison; drinks carry caffeine.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-29
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        -- `>= x` becomes min x, `<= x` max x; dropping the columns drops their old CHECKs too
        ALTER TABLE habit ADD COLUMN min_value double precision, ADD COLUMN max_value double precision;
        UPDATE habit SET min_value = threshold WHERE comparator = '>=';
        UPDATE habit SET max_value = threshold WHERE comparator = '<=';
        ALTER TABLE habit DROP COLUMN comparator, DROP COLUMN threshold;
        -- a metric needs at least one bound; manual habits have none. min <= max is checked by the
        -- API, because clock metrics order across midnight (23:40 .. 00:20).
        ALTER TABLE habit ADD CONSTRAINT habit_rule_check CHECK (
            CASE WHEN metric IS NULL THEN min_value IS NULL AND max_value IS NULL
                 ELSE min_value IS NOT NULL OR max_value IS NOT NULL END);

        -- Caffeine is a property of the entry (tea can be herbal); coffee and tea default to yes.
        ALTER TABLE drink ADD COLUMN caffeine boolean;
        UPDATE drink SET caffeine = drink IN ('coffee', 'tea');
        ALTER TABLE drink ALTER COLUMN caffeine SET NOT NULL;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE drink DROP COLUMN caffeine;
        ALTER TABLE habit DROP CONSTRAINT habit_rule_check;
        ALTER TABLE habit ADD COLUMN comparator text CHECK (comparator IN ('>=', '<=')),
                          ADD COLUMN threshold double precision;
        -- a two-sided range cannot be expressed; its lower bound is kept
        UPDATE habit SET comparator = '>=', threshold = min_value WHERE min_value IS NOT NULL;
        UPDATE habit SET comparator = '<=', threshold = max_value
            WHERE min_value IS NULL AND max_value IS NOT NULL;
        ALTER TABLE habit DROP COLUMN min_value, DROP COLUMN max_value;
        ALTER TABLE habit ADD CHECK (
            (metric IS NULL) = (comparator IS NULL) AND (metric IS NULL) = (threshold IS NULL));
    """)
