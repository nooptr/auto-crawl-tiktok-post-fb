"""Baseline schema for social automation.

Revision ID: 20260328_01
Revises:
Create Date: 2026-03-28 23:45:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260328_01"
down_revision = None
branch_labels = None
depends_on = None


CAMPAIGN_STATUS = ("active", "paused")
VIDEO_STATUS = ("pending", "downloading", "ready", "posted", "failed")
INTERACTION_STATUS = ("pending", "replied", "failed")
TASK_STATUS = ("queued", "processing", "completed", "failed")


def _inspector(bind):
    return sa.inspect(bind)


def _has_table(bind, table_name: str) -> bool:
    return _inspector(bind).has_table(table_name)


def _has_column(bind, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in _inspector(bind).get_columns(table_name))


def _has_unique(bind, table_name: str, constraint_name: str) -> bool:
    return any(item["name"] == constraint_name for item in _inspector(bind).get_unique_constraints(table_name))


def _drop_unique_if_exists(bind, table_name: str, constraint_name: str):
    if _has_unique(bind, table_name, constraint_name):
        op.drop_constraint(constraint_name, table_name, type_="unique")


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


def _campaign_status_enum():
    return postgresql.ENUM(*CAMPAIGN_STATUS, name="campaignstatus", create_type=False)


def _video_status_enum():
    return postgresql.ENUM(*VIDEO_STATUS, name="videostatus", create_type=False)


def _interaction_status_enum():
    return postgresql.ENUM(*INTERACTION_STATUS, name="interactionstatus", create_type=False)


def _task_status_enum():
    return postgresql.ENUM(*TASK_STATUS, name="taskstatus", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    _create_enum_if_needed(bind, "campaignstatus", CAMPAIGN_STATUS)
    _create_enum_if_needed(bind, "videostatus", VIDEO_STATUS)
    _create_enum_if_needed(bind, "interactionstatus", INTERACTION_STATUS)
    _create_enum_if_needed(bind, "taskstatus", TASK_STATUS)

    if not _has_table(bind, "campaigns"):
        op.create_table(
            "campaigns",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("source_url", sa.String(), nullable=True),
            sa.Column("status", _campaign_status_enum(), nullable=True),
            sa.Column("auto_post", sa.Boolean(), nullable=True),
            sa.Column("target_page_id", sa.String(), nullable=True),
            sa.Column("schedule_interval", sa.Integer(), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(), nullable=True),
            sa.Column("last_sync_status", sa.String(), server_default="idle", nullable=True),
            sa.Column("last_sync_error", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_campaigns_name", "campaigns", ["name"], unique=False)
    else:
        if not _has_column(bind, "campaigns", "last_synced_at"):
            op.add_column("campaigns", sa.Column("last_synced_at", sa.DateTime(), nullable=True))
        if not _has_column(bind, "campaigns", "last_sync_status"):
            op.add_column("campaigns", sa.Column("last_sync_status", sa.String(), server_default="idle", nullable=True))
        if not _has_column(bind, "campaigns", "last_sync_error"):
            op.add_column("campaigns", sa.Column("last_sync_error", sa.String(), nullable=True))

    if not _has_table(bind, "facebook_pages"):
        op.create_table(
            "facebook_pages",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("page_id", sa.String(), nullable=True),
            sa.Column("page_name", sa.String(), nullable=True),
            sa.Column("long_lived_access_token", sa.String(), nullable=True),
            sa.UniqueConstraint("page_id"),
        )
        op.create_index("ix_facebook_pages_page_id", "facebook_pages", ["page_id"], unique=False)

    if not _has_table(bind, "videos"):
        op.create_table(
            "videos",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("campaign_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("campaigns.id"), nullable=True),
            sa.Column("original_id", sa.String(), nullable=True),
            sa.Column("source_video_url", sa.String(), nullable=True),
            sa.Column("file_path", sa.String(), nullable=True),
            sa.Column("original_caption", sa.String(), nullable=True),
            sa.Column("ai_caption", sa.String(), nullable=True),
            sa.Column("status", _video_status_enum(), nullable=True),
            sa.Column("publish_time", sa.DateTime(), nullable=True),
            sa.Column("fb_post_id", sa.String(), nullable=True),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("retry_count", sa.Integer(), server_default="0", nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
            sa.UniqueConstraint("campaign_id", "original_id", name="uq_videos_campaign_original"),
        )
        op.create_index("ix_videos_original_id", "videos", ["original_id"], unique=False)
    else:
        if not _has_column(bind, "videos", "source_video_url"):
            op.add_column("videos", sa.Column("source_video_url", sa.String(), nullable=True))
        if not _has_column(bind, "videos", "last_error"):
            op.add_column("videos", sa.Column("last_error", sa.String(), nullable=True))
        if not _has_column(bind, "videos", "retry_count"):
            op.add_column("videos", sa.Column("retry_count", sa.Integer(), server_default="0", nullable=True))
        if not _has_column(bind, "videos", "created_at"):
            op.add_column("videos", sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True))
        if not _has_column(bind, "videos", "updated_at"):
            op.add_column("videos", sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True))

        for constraint_name in ("videos_original_id_key", "uq_videos_original_id", "videos_original_id_unique"):
            _drop_unique_if_exists(bind, "videos", constraint_name)

        if not _has_unique(bind, "videos", "uq_videos_campaign_original"):
            op.create_unique_constraint("uq_videos_campaign_original", "videos", ["campaign_id", "original_id"])

        op.execute(
            sa.text(
                """
                UPDATE videos
                SET last_error = fb_post_id
                WHERE status = 'failed' AND last_error IS NULL AND fb_post_id IS NOT NULL
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE videos
                SET fb_post_id = NULL
                WHERE status = 'failed' AND last_error IS NOT NULL
                """
            )
        )

    if not _has_table(bind, "interactions_log"):
        op.create_table(
            "interactions_log",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("page_id", sa.String(), sa.ForeignKey("facebook_pages.page_id"), nullable=True),
            sa.Column("post_id", sa.String(), nullable=True),
            sa.Column("comment_id", sa.String(), nullable=True),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("user_message", sa.String(), nullable=True),
            sa.Column("ai_reply", sa.String(), nullable=True),
            sa.Column("status", _interaction_status_enum(), nullable=True),
            sa.UniqueConstraint("comment_id"),
        )

    if not _has_table(bind, "task_queue"):
        op.create_table(
            "task_queue",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("task_type", sa.String(), nullable=True),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("status", _task_status_enum(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=True),
            sa.Column("locked_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_task_queue_task_type", "task_queue", ["task_type"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_unique(bind, "videos", "uq_videos_campaign_original"):
        op.drop_constraint("uq_videos_campaign_original", "videos", type_="unique")
