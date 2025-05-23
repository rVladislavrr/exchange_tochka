from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: str
    DB_NAME: str
    DB_USER: str
    DB_PASS: str

    REDIS_USER_PASSWORD: str
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    ADMIN_API_KEY: str

    model_config = SettingsConfigDict(env_file=".env")

    def DATABASE_URL(self):
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def DATABASE_URL_alembic(self):
        return f"postgresql://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

    @property
    def REDIS_BASE_URL(self) -> str:
        return f"redis://:{self.REDIS_USER_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}"


settings = Settings()
