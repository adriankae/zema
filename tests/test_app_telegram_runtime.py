from __future__ import annotations

from app.core.database import SessionLocal
from app.models import Account, TelegramBotSettings
from app.telegram_runtime import TelegramBotManager


def _account() -> Account:
    db = SessionLocal()
    try:
        return db.query(Account).filter(Account.username == "admin").one()
    finally:
        db.close()


def _settings(**overrides) -> int:
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        values = {
            "account_id": account.id,
            "bot_token_encrypted": "encrypted-token",
            "bot_username": "zema_bot",
            "allowed_chat_ids": [123],
            "allowed_user_ids": [],
            "is_enabled": True,
            "runtime_status": "stopped",
        }
        values.update(overrides)
        row = TelegramBotSettings(**values)
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def test_reconcile_marks_disabled_running_bot_as_stopping(monkeypatch):
    settings_id = _settings(is_enabled=False, runtime_status="active")
    account = _account()
    manager = TelegramBotManager()
    monkeypatch.setattr(manager, "is_running", lambda account_id: account_id == account.id)
    monkeypatch.setattr(manager, "_stop_worker", lambda account_id: None)

    manager.reconcile()

    db = SessionLocal()
    try:
        row = db.get(TelegramBotSettings, settings_id)
        assert row is not None
        assert row.is_enabled is False
        assert row.runtime_status == "stopping"
        assert row.runtime_last_seen_at is not None
    finally:
        db.close()


def test_reconcile_records_start_failure_as_needs_attention(monkeypatch):
    settings_id = _settings(is_enabled=True, runtime_status="starting")
    manager = TelegramBotManager()

    def fail_start(_db, _account):
        raise RuntimeError("boom")

    monkeypatch.setattr(manager, "start_for_account", fail_start)

    manager.reconcile()

    db = SessionLocal()
    try:
        row = db.get(TelegramBotSettings, settings_id)
        assert row is not None
        assert row.is_enabled is False
        assert row.runtime_status == "needs_attention"
        assert row.last_error == "boom"
        assert row.runtime_last_seen_at is not None
    finally:
        db.close()


def test_reconcile_stops_worker_after_settings_reset(monkeypatch):
    stopped: list[int] = []
    manager = TelegramBotManager()
    monkeypatch.setattr(manager, "_running_account_ids", lambda: {999})
    monkeypatch.setattr(manager, "_stop_worker", stopped.append)

    manager.reconcile()

    assert stopped == [999]
