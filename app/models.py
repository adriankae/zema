from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    api_keys: Mapped[list["AccountApiKey"]] = relationship(back_populates="account")
    subjects: Mapped[list["Subject"]] = relationship(back_populates="account")
    body_locations: Mapped[list["BodyLocation"]] = relationship(back_populates="account")
    episodes: Mapped[list["EczemaEpisode"]] = relationship(back_populates="account")


class AccountApiKey(Base):
    __tablename__ = "account_api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped[Account] = relationship(back_populates="api_keys")


class TelegramBotSettings(Base):
    __tablename__ = "telegram_bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    bot_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    bot_username: Mapped[str | None] = mapped_column(String(150), nullable=True)
    allowed_chat_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    allowed_user_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    allow_writes: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    allow_adherence_rebuild: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("account_api_keys.id", ondelete="SET NULL"), nullable=True)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'stopped'"))
    runtime_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    account: Mapped[Account] = relationship(back_populates="subjects")


class BodyLocation(Base):
    __tablename__ = "body_locations"
    __table_args__ = (UniqueConstraint("account_id", "code", name="uq_body_locations_account_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    image_storage_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    image_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped[Account] = relationship(back_populates="body_locations")


class TaperProtocolPhase(Base):
    __tablename__ = "taper_protocol_phases"

    phase_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    apply_every_n_days: Mapped[int] = mapped_column(Integer, nullable=False)
    applications_per_day: Mapped[int] = mapped_column(Integer, nullable=False)


class EczemaEpisode(Base):
    __tablename__ = "eczema_episodes"
    __table_args__ = (
        Index(
            "uq_eczema_episodes_subject_location_active",
            "subject_id",
            "location_id",
            unique=True,
            sqlite_where=text("status != 'obsolete'"),
            postgresql_where=text("status != 'obsolete'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("body_locations.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    current_phase_number: Mapped[int] = mapped_column(Integer, nullable=False)
    phase_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    phase_due_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    protocol_version: Mapped[str] = mapped_column(String(50), nullable=False)
    healed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    obsolete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    account: Mapped[Account] = relationship(back_populates="episodes")


class EpisodePhaseHistory(Base):
    __tablename__ = "episode_phase_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("eczema_episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    phase_number: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class TreatmentApplication(Base):
    __tablename__ = "treatment_applications"
    __table_args__ = (UniqueConstraint("episode_id", "applied_at", name="uq_treatment_applications_episode_applied_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("eczema_episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    treatment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    treatment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quantity_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phase_number_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_voided: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class EpisodeDailyAdherence(Base):
    __tablename__ = "episode_daily_adherence"
    __table_args__ = (
        UniqueConstraint("episode_id", "date", name="uq_episode_daily_adherence_episode_date"),
        CheckConstraint("expected_applications >= 0", name="ck_episode_daily_adherence_expected_nonnegative"),
        CheckConstraint("completed_applications >= 0", name="ck_episode_daily_adherence_completed_nonnegative"),
        CheckConstraint("credited_applications >= 0", name="ck_episode_daily_adherence_credited_nonnegative"),
        CheckConstraint("credited_applications <= expected_applications", name="ck_episode_daily_adherence_credited_lte_expected"),
        CheckConstraint(
            "status in ('completed', 'partial', 'missed', 'not_due', 'future')",
            name="ck_episode_daily_adherence_status",
        ),
        CheckConstraint(
            "source in ('calculated', 'backfill', 'rebuild', 'system')",
            name="ck_episode_daily_adherence_source",
        ),
        Index("ix_episode_daily_adherence_account_date", "account_id", "date"),
        Index("ix_episode_daily_adherence_episode_date", "episode_id", "date"),
        Index("ix_episode_daily_adherence_subject_date", "subject_id", "date"),
        Index("ix_episode_daily_adherence_location_date", "location_id", "date"),
        Index("ix_episode_daily_adherence_status_date", "status", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    episode_id: Mapped[int] = mapped_column(ForeignKey("eczema_episodes.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False)
    location_id: Mapped[int] = mapped_column(ForeignKey("body_locations.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    phase_number: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_applications: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_applications: Mapped[int] = mapped_column(Integer, nullable=False)
    credited_applications: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class EpisodeEvent(Base):
    __tablename__ = "episode_events"
    __table_args__ = (
        Index("ix_episode_events_episode_id_occurred_at", "episode_id", "occurred_at", "id"),
        Index("ix_episode_events_event_type_occurred_at", "event_type", "occurred_at", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    episode_id: Mapped[int] = mapped_column(ForeignKey("eczema_episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
