"""Supplements: what is taken how many times a day, and the intakes per day.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-29
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE supplement (
            id           uuid PRIMARY KEY,
            name         text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
            brand        text,
            form         text NOT NULL DEFAULT 'capsule'
                         CHECK (form IN ('capsule', 'tablet', 'drops', 'powder', 'other')),
            per_day      int NOT NULL DEFAULT 1 CHECK (per_day BETWEEN 1 AND 50),  -- "Y" units a day
            -- active ingredients per unit: [{"key": "vitamin_d", "amount": 1000}] (catalog key,
            -- unit from the catalog) or [{"name": "Ashwagandha", "amount": 300, "unit": "mg"}]
            ingredients  jsonb NOT NULL DEFAULT '[]',
            timing       text,                  -- free text, e.g. "morgens zum Frühstück"
            note         text,
            position     int NOT NULL DEFAULT 0,
            archived     boolean NOT NULL DEFAULT false,
            created_at   timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE supplement_log (
            supplement_id uuid NOT NULL REFERENCES supplement ON DELETE CASCADE,
            day           date NOT NULL,
            count         int NOT NULL CHECK (count BETWEEN 1 AND 100),  -- "X" units taken
            updated_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (supplement_id, day)
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE supplement_log; DROP TABLE supplement;")
