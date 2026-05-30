from pathlib import Path

from app.core.database import SessionLocal
from app.core.security import hash_password, verify_password
from app.models import Account, TelegramBotSettings
from app.services import bootstrap_data
from app.telegram_settings import decrypt_secret, encrypt_secret


def test_bootstrap_data_does_not_reset_existing_account_password():
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        account.username = "changed-admin"
        account.password_hash = hash_password("kept-password")
        db.add(account)
        db.commit()

        bootstrap_data(db)

        accounts = db.query(Account).order_by(Account.id.asc()).all()
        assert len(accounts) == 1
        assert accounts[0].username == "changed-admin"
        assert verify_password("kept-password", accounts[0].password_hash)
        assert not verify_password("admin", accounts[0].password_hash)
    finally:
        db.close()


def test_bootstrap_data_does_not_reset_existing_telegram_settings():
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        row = TelegramBotSettings(
            account_id=account.id,
            bot_token_encrypted=encrypt_secret("123456:test-token"),
            bot_username="zema_bot",
            allowed_chat_ids=[111],
            allowed_user_ids=[222],
            allow_writes=False,
            allow_adherence_rebuild=True,
            is_enabled=True,
            runtime_status="active",
        )
        db.add(row)
        db.commit()

        bootstrap_data(db)

        rows = db.query(TelegramBotSettings).all()
        assert len(rows) == 1
        refreshed = rows[0]
        assert decrypt_secret(refreshed.bot_token_encrypted) == "123456:test-token"
        assert refreshed.bot_username == "zema_bot"
        assert refreshed.allowed_chat_ids == [111]
        assert refreshed.allowed_user_ids == [222]
        assert refreshed.allow_writes is False
        assert refreshed.allow_adherence_rebuild is True
        assert refreshed.is_enabled is True
        assert refreshed.runtime_status == "active"
    finally:
        db.close()


def test_docker_compose_project_name_is_stable_for_volume_persistence():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    update_script = Path("scripts/update.sh").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "name: ${COMPOSE_PROJECT_NAME:-zema}" in compose
    assert "container_name: zema-be" in compose
    assert "container_name: zema-telegram" in compose
    assert "name: zema-postgres-data" in compose
    assert "name: zema-location-images" in compose
    assert "      - telegram" not in compose
    assert 'export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-zema}"' in update_script
    assert "COMPOSE_PROJECT_NAME=zema" in env_example
