from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import generate_api_key, hash_api_key
from app.core.time import utc_now
from app.models import Account, AccountApiKey, TelegramBotSettings
from czm_cli.telegram.setup import DiscoveredChat, discover_chats, validate_bot_token


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="telegram secret cannot be decrypted") from exc


@dataclass(slots=True)
class TelegramSettingsView:
    configured: bool
    enabled: bool
    running: bool
    bot_username: str | None
    allowed_chat_ids: list[int]
    allowed_user_ids: list[int]
    allow_writes: bool
    allow_adherence_rebuild: bool
    last_error: str | None
    discovered_chats: list[DiscoveredChat]
    notice: str | None = None

    @property
    def current_step(self) -> int:
        if not self.configured:
            return 1
        if not self.allowed_chat_ids:
            return 2
        return 3

    @property
    def allowed_chat_ids_text(self) -> str:
        return ", ".join(str(value) for value in self.allowed_chat_ids)

    @property
    def allowed_user_ids_text(self) -> str:
        return ", ".join(str(value) for value in self.allowed_user_ids)


def get_telegram_settings(db: Session, account: Account) -> TelegramBotSettings | None:
    return db.execute(select(TelegramBotSettings).where(TelegramBotSettings.account_id == account.id)).scalar_one_or_none()


def telegram_settings_view(db: Session, account: Account, *, running: bool = False) -> TelegramSettingsView:
    row = get_telegram_settings(db, account)
    if row is None:
        return TelegramSettingsView(False, False, running, None, [], [], True, False, None, [])
    return TelegramSettingsView(
        configured=bool(row.bot_token_encrypted),
        enabled=row.is_enabled,
        running=running,
        bot_username=row.bot_username,
        allowed_chat_ids=list(row.allowed_chat_ids or []),
        allowed_user_ids=list(row.allowed_user_ids or []),
        allow_writes=row.allow_writes,
        allow_adherence_rebuild=row.allow_adherence_rebuild,
        last_error=row.last_error,
        discovered_chats=[],
    )


def parse_int_list(value: str | None, *, label: str) -> list[int]:
    if not value or not value.strip():
        return []
    items = value.replace("\n", ",").split(",")
    parsed: list[int] = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        try:
            parsed.append(int(item))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{label} must contain numeric ids") from exc
    return list(dict.fromkeys(parsed))


def save_telegram_settings(
    db: Session,
    account: Account,
    *,
    bot_token: str | None,
    allowed_chat_ids: list[int],
    allowed_user_ids: list[int],
    allow_writes: bool,
    allow_adherence_rebuild: bool,
    is_enabled: bool | None = None,
) -> TelegramBotSettings:
    row = get_telegram_settings(db, account)
    if row is None:
        row = TelegramBotSettings(account_id=account.id, allowed_chat_ids=[], allowed_user_ids=[])
        db.add(row)
    cleaned_token = bot_token.strip() if bot_token else ""
    if cleaned_token:
        row.bot_token_encrypted = encrypt_secret(cleaned_token)
        row.bot_username = None
        row.last_error = None
    row.allowed_chat_ids = allowed_chat_ids
    row.allowed_user_ids = allowed_user_ids
    row.allow_writes = allow_writes
    row.allow_adherence_rebuild = allow_adherence_rebuild
    if is_enabled is not None:
        row.is_enabled = is_enabled
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def save_telegram_bot_token(db: Session, account: Account, bot_token: str) -> TelegramBotSettings:
    cleaned_token = bot_token.strip()
    if not cleaned_token:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bot token is required")
    try:
        bot = validate_bot_token(cleaned_token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    row = get_telegram_settings(db, account)
    if row is None:
        row = TelegramBotSettings(account_id=account.id, allowed_chat_ids=[], allowed_user_ids=[])
        db.add(row)
    row.bot_token_encrypted = encrypt_secret(cleaned_token)
    row.bot_username = bot.username
    row.last_error = None
    row.is_enabled = False
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def discover_telegram_chats(db: Session, account: Account) -> list[DiscoveredChat]:
    row = get_telegram_settings(db, account)
    token = decrypt_secret(row.bot_token_encrypted if row else None)
    if not token:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="save a bot token first")
    try:
        return discover_chats(token)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def set_telegram_enabled(db: Session, account: Account, enabled: bool) -> TelegramBotSettings:
    row = get_telegram_settings(db, account)
    if row is None or not row.bot_token_encrypted:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="save a bot token first")
    if enabled and not row.allowed_chat_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="select an allowed chat first")
    if enabled:
        ensure_telegram_api_key(db, account, row)
    row.is_enabled = enabled
    row.last_error = None
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def reset_telegram_settings(db: Session, account: Account) -> None:
    row = get_telegram_settings(db, account)
    if row is None:
        return
    api_key = db.get(AccountApiKey, row.api_key_id) if row.api_key_id else None
    db.delete(row)
    if api_key is not None:
        db.delete(api_key)
    db.commit()


def ensure_telegram_api_key(db: Session, account: Account, row: TelegramBotSettings) -> str:
    existing = decrypt_secret(row.api_key_encrypted)
    if existing:
        return existing
    plaintext = generate_api_key()
    api_key = AccountApiKey(
        account_id=account.id,
        name="telegram-dashboard-bot",
        key_hash=hash_api_key(plaintext),
        is_active=True,
    )
    db.add(api_key)
    db.flush()
    row.api_key_id = api_key.id
    row.api_key_encrypted = encrypt_secret(plaintext)
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return plaintext
