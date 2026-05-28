from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+pysqlite:///./eczema.db"
    app_env: str = "local"
    deployment_timezone: str = "UTC"
    api_port: int = 28173
    jwt_secret: str = Field(default="dev-secret-change-me")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    enable_scheduler: bool = True
    initial_username: str = "admin"
    initial_password: str = "admin"
    location_image_dir: str = "./location-images"
    location_image_max_bytes: int = 5 * 1024 * 1024
    zema_host_bind: str = "127.0.0.1"
    zema_port: int = 28173
    telegram_backend_url: str = "http://127.0.0.1:28173"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
