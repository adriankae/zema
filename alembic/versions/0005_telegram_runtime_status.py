"""Add Telegram runtime status fields.

Revision ID: 0005_telegram_runtime_status
Revises: 0004_telegram_bot_settings
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_telegram_runtime_status"
down_revision = "0004_telegram_bot_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("telegram_bot_settings", sa.Column("runtime_status", sa.String(length=30), server_default="stopped", nullable=False))
    op.add_column("telegram_bot_settings", sa.Column("runtime_last_seen_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("telegram_bot_settings", "runtime_last_seen_at")
    op.drop_column("telegram_bot_settings", "runtime_status")
