"""Meal templates: a saved combination of foods (Waistline's "meals") that is logged as one entry
per food, so each amount can still be changed. Dishes (foods with components) stay one entry.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-04
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE meal_template (
            id          uuid PRIMARY KEY,
            name        text NOT NULL CHECK (name <> ''),
            source      text NOT NULL DEFAULT 'vitalocal' CHECK (source IN ('vitalocal', 'waistline')),
            source_ref  text,
            favorite    boolean NOT NULL DEFAULT false,
            archived    boolean NOT NULL DEFAULT false,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX meal_template_source_ref_idx ON meal_template (source, source_ref)
            WHERE source_ref IS NOT NULL;
        CREATE INDEX meal_template_updated_idx ON meal_template (updated_at);

        -- amount in the food's base unit, as for diary entries
        CREATE TABLE meal_template_item (
            template_id uuid NOT NULL REFERENCES meal_template ON DELETE CASCADE,
            position    int NOT NULL,
            food_id     uuid NOT NULL REFERENCES food,
            amount      double precision NOT NULL CHECK (amount > 0),
            quantity    double precision NOT NULL DEFAULT 1 CHECK (quantity > 0),
            PRIMARY KEY (template_id, position)
        );
        CREATE INDEX meal_template_item_food_idx ON meal_template_item (food_id);

        -- which template an entry came from (shown in the diary, counted as use of the template)
        ALTER TABLE nutrition_entry ADD COLUMN template_id uuid REFERENCES meal_template ON DELETE SET NULL;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE nutrition_entry DROP COLUMN template_id;
        DROP TABLE meal_template_item; DROP TABLE meal_template;
    """)
