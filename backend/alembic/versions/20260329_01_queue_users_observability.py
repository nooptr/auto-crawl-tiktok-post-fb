"""Add users, queue observability, and worker state.

Revision ID: 20260329_01
Revises: 20260328_01
Create Date: 2026-03-29 01:30:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260329_01"
down_revision = "20260328_01"
branch_labels = None
depends_on = None


USER_ROLE = ("admin", "operator")


def _inspector(bind):
    return sa.inspect(bind)


def _has_table(bind, table_name: str) -> bool:
    return _inspector(bind).has_table(table_name)


def _has_column(bind, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in _inspector(bind).get_columns(table_name))


def _has_index(bind, table_name: str, index_name: str) -> bool:
    return any(item["name"] == index_name for item in _inspector(bind).get_indexes(table_name))


def _create_enum_if_needed(bind, name: str, values: tuple[str, ...]):
    if bind.dialect.name != "postgresql":
        return
    values_sql = ", ".join(f"'{value}'" for value in values)
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{name}') THEN
                    CREATE TYPE {name} AS ENUM ({values_sql});
                END IF;
            END$$;
            """
        )
    )


def _user_role_enum():
    return postgresql.ENUM(*USER_ROLE, name="userrole", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    _create_enum_if_needed(bind, "userrole", USER_ROLE)

    if _has_table(bind, "facebook_pages"):
        if not _has_column(bind, "facebook_pages", "created_at"):
            op.add_column("facebook_pages", sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True))
        if not _has_column(bind, "facebook_pages", "updated_at"):
            op.add_column("facebook_pages", sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True))

    if _has_table(bind, "interactions_log"):
        if not _has_column(bind, "interactions_log", "created_at"):
            op.add_column("interactions_log", sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True))
        if not _has_column(bind, "interactions_log", "updated_at"):
            op.add_column("interactions_log", sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True))

    if _has_table(bind, "task_queue"):
        additions = [
            ("entity_type", sa.String(), None),
            ("entity_id", sa.String(), None),
            ("priority", sa.Integer(), "0"),
            ("max_attempts", sa.Integer(), "3"),
            ("last_error", sa.String(), None),
            ("available_at", sa.DateTime(), "CURRENT_TIMESTAMP"),
            ("locked_by", sa.String(), None),
            ("started_at", sa.DateTime(), None),
            ("completed_at", sa.DateTime(), None),
            ("created_at", sa.DateTime(), "CURRENT_TIMESTAMP"),
            ("updated_at", sa.DateTime(), "CURRENT_TIMESTAMP"),
        ]
        for column_name, column_type, default_value in additions:
            if not _has_column(bind, "task_queue", column_name):
                kwargs = {"nullable": True}
                if default_value:
                    kwargs["server_default"] = sa.text(default_value)
                op.add_column("task_queue", sa.Column(column_name, column_type, **kwargs))

        if not _has_index(bind, "task_queue", "ix_task_queue_entity_type"):
            op.create_index("ix_task_queue_entity_type", "task_queue", ["entity_type"], unique=False)
        if not _has_index(bind, "task_queue", "ix_task_queue_entity_id"):
            op.create_index("ix_task_queue_entity_id", "task_queue", ["entity_id"], unique=False)

    if not _has_table(bind, "users"):
        op.create_table(
            "users",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("username", sa.String(), nullable=False),
            sa.Column("display_name", sa.String(), nullable=True),
            sa.Column("password_hash", sa.String(), nullable=False),
            sa.Column("role", _user_role_enum(), nullable=False, server_default="admin"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("username"),
        )
        op.create_index("ix_users_username", "users", ["username"], unique=False)

    if not _has_table(bind, "worker_heartbeats"):
        op.create_table(
            "worker_heartbeats",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("worker_name", sa.String(), nullable=False),
            sa.Column("app_role", sa.String(), nullable=False),
            sa.Column("hostname", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="idle"),
            sa.Column("current_task_id", sa.String(), nullable=True),
            sa.Column("current_task_type", sa.String(), nullable=True),
            sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("worker_name"),
        )
        op.create_index("ix_worker_heartbeats_worker_name", "worker_heartbeats", ["worker_name"], unique=False)

    if not _has_table(bind, "system_events"):
        op.create_table(
            "system_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("scope", sa.String(), nullable=False),
            sa.Column("level", sa.String(), nullable=False),
            sa.Column("message", sa.String(), nullable=False),
            sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("actor_user_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_system_events_scope", "system_events", ["scope"], unique=False)
        op.create_index("ix_system_events_level", "system_events", ["level"], unique=False)
        op.create_index("ix_system_events_created_at", "system_events", ["created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "system_events"):
        op.drop_table("system_events")
    if _has_table(bind, "worker_heartbeats"):
        op.drop_table("worker_heartbeats")
    if _has_table(bind, "users"):
        op.drop_table("users")
