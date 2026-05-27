from __future__ import annotations

import logging
import asyncio
import threading
import time
from asyncio import AbstractEventLoop
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.models import Account, TelegramBotSettings
from app.telegram_settings import decrypt_secret, ensure_telegram_api_key, get_telegram_settings

from czm_cli.config import AppConfig, TelegramConfig
from czm_cli.client import CzmClient
from czm_cli.telegram.runtime import TelegramRuntime, build_application
from czm_cli.telegram.config import validate_telegram_config
from czm_cli.telegram.setup import validate_bot_token


logger = logging.getLogger(__name__)
RECONCILE_INTERVAL_SECONDS = 5


@dataclass(slots=True)
class BotWorker:
    thread: threading.Thread
    application: Any | None = None
    loop: AbstractEventLoop | None = None


class TelegramBotManager:
    def __init__(self) -> None:
        self._workers: dict[int, BotWorker] = {}
        self._lock = threading.Lock()

    def is_running(self, account_id: int) -> bool:
        worker = self._workers.get(account_id)
        return bool(worker and worker.thread.is_alive())

    def start_for_account(self, db: Session, account: Account) -> None:
        row = get_telegram_settings(db, account)
        if row is None:
            raise ValueError("telegram settings not configured")
        token = decrypt_secret(row.bot_token_encrypted)
        if not token:
            raise ValueError("telegram bot token is required")
        if not row.allowed_chat_ids:
            raise ValueError("at least one allowed chat id is required")
        with self._lock:
            worker = self._workers.get(account.id)
            if worker and worker.thread.is_alive():
                return
        api_key = ensure_telegram_api_key(db, account, row)
        bot = validate_bot_token(token)
        row.bot_username = bot.username
        row.is_enabled = True
        row.last_error = None
        row.started_at = utc_now()
        row.updated_at = row.started_at
        db.add(row)
        db.commit()
        with self._lock:
            thread = threading.Thread(
                target=self._run,
                args=(account.id, token, api_key, list(row.allowed_chat_ids or []), list(row.allowed_user_ids or []), row.allow_writes, row.allow_adherence_rebuild),
                name=f"zema-telegram-{account.id}",
                daemon=True,
            )
            self._workers[account.id] = BotWorker(thread=thread)
            thread.start()
            logger.info("Started Telegram bot worker for account %s as @%s", account.id, bot.username)

    def stop_for_account(self, db: Session, account: Account) -> None:
        self._stop_worker(account.id)
        row = get_telegram_settings(db, account)
        if row is None:
            return
        row.is_enabled = False
        row.stopped_at = utc_now()
        row.updated_at = row.stopped_at
        db.add(row)
        db.commit()

    def start_enabled(self) -> None:
        self.reconcile()

    def reconcile(self) -> None:
        db = SessionLocal()
        try:
            rows = list(db.query(TelegramBotSettings).all())
            configured_account_ids = {row.account_id for row in rows}
            for row in rows:
                account = db.get(Account, row.account_id)
                if account is None:
                    continue
                if not row.is_enabled:
                    if self.is_running(row.account_id):
                        self.stop_for_account(db, account)
                        logger.info("Stopped Telegram bot worker for disabled account %s", row.account_id)
                    continue
                try:
                    self.start_for_account(db, account)
                except Exception as exc:
                    row.last_error = str(exc)
                    row.is_enabled = False
                    row.updated_at = utc_now()
                    db.add(row)
                    db.commit()
                    logger.exception("Failed to start Telegram bot for account %s", row.account_id)
            for account_id in self._running_account_ids() - configured_account_ids:
                self._stop_worker(account_id)
                logger.info("Stopped Telegram bot worker for reset account %s", account_id)
            self._forget_stopped_workers()
        finally:
            db.close()

    def _running_account_ids(self) -> set[int]:
        with self._lock:
            return {account_id for account_id, worker in self._workers.items() if worker.thread.is_alive()}

    def _stop_worker(self, account_id: int) -> None:
        with self._lock:
            worker = self._workers.get(account_id)
            if worker and worker.loop is not None and worker.loop.is_running():
                worker.loop.call_soon_threadsafe(worker.loop.stop)

    def _forget_stopped_workers(self) -> None:
        with self._lock:
            stopped = [account_id for account_id, worker in self._workers.items() if not worker.thread.is_alive()]
            for account_id in stopped:
                self._workers.pop(account_id, None)

    def _run(
        self,
        account_id: int,
        token: str,
        api_key: str,
        allowed_chat_ids: list[int],
        allowed_user_ids: list[int],
        allow_writes: bool,
        allow_adherence_rebuild: bool,
    ) -> None:
        config = AppConfig(
            base_url=settings.telegram_backend_url,
            api_key=api_key,
            timezone=settings.deployment_timezone,
            telegram=TelegramConfig(
                bot_token=token,
                allowed_chat_ids=allowed_chat_ids,
                allowed_user_ids=allowed_user_ids,
                allow_writes=allow_writes,
                allow_adherence_rebuild=allow_adherence_rebuild,
            ),
        )
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            validate_telegram_config(config)
            validate_bot_token(token)
            client = CzmClient(config.base_url, config.api_key or "")
            try:
                application = build_application(TelegramRuntime(config=config, client=client))
                with self._lock:
                    worker = self._workers.get(account_id)
                    if worker is not None:
                        worker.application = application
                        worker.loop = loop
                logger.info("Telegram polling started for account %s", account_id)
                application.run_polling(stop_signals=None)
                logger.info("Telegram polling stopped for account %s", account_id)
            finally:
                client.close()
        except Exception as exc:
            logger.exception("Telegram bot stopped for account %s", account_id)
            db = SessionLocal()
            try:
                row = db.query(TelegramBotSettings).filter(TelegramBotSettings.account_id == account_id).one_or_none()
                if row is not None:
                    row.last_error = str(exc)
                    row.is_enabled = False
                    row.stopped_at = utc_now()
                    row.updated_at = row.stopped_at
                    db.add(row)
                    db.commit()
            finally:
                db.close()


telegram_bot_manager = TelegramBotManager()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logger.info("Starting Zema Telegram runtime")
    while True:
        telegram_bot_manager.reconcile()
        time.sleep(RECONCILE_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
