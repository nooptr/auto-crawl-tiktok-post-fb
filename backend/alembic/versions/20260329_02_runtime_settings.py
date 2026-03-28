"""Add runtime settings table.

Revision ID: 20260329_02
Revises: 20260329_01
Create Date: 2026-03-29 18:20:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260329_02"
down_revision = "20260329_01"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "runtime_settings"):
        return

    op.create_table(
        "runtime_settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), nullable=True),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "runtime_settings"):
        op.drop_table("runtime_settings")
