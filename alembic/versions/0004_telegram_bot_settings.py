"""add telegram bot settings

Revision ID: 0004_telegram_bot_settings
Revises: 0003_location_images
Create Date: 2026-05-27 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004_telegram_bot_settings"
down_revision = "0003_location_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_bot_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("bot_token_encrypted", sa.Text(), nullable=True),
        sa.Column("bot_username", sa.String(length=150), nullable=True),
        sa.Column("allowed_chat_ids", sa.JSON(), nullable=False),
        sa.Column("allowed_user_ids", sa.JSON(), nullable=False),
        sa.Column("allow_writes", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("allow_adherence_rebuild", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["api_key_id"], ["account_api_keys.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id"),
    )
    op.create_index("ix_telegram_bot_settings_account_id", "telegram_bot_settings", ["account_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_telegram_bot_settings_account_id", table_name="telegram_bot_settings")
    op.drop_table("telegram_bot_settings")
