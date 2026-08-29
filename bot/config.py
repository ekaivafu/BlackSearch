import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import List

class Settings(BaseSettings):
    bot_token: str = Field(..., env="BOT_TOKEN")
    database_url: str = Field(..., env="DATABASE_URL")
    admin_telegram_ids: str = Field(..., env="ADMIN_TELEGRAM_IDS")
    initial_credits: int = Field(5, env="INITIAL_CREDITS")
    default_recharge_amount: int = Field(10, env="DEFAULT_RECHARGE_AMOUNT")
    log_level: str = Field("INFO", env="LOG_LEVEL")

    @property
    def admin_ids(self) -> List[int]:
        if not self.admin_telegram_ids:
            return []
        return [int(uid.strip()) for uid in self.admin_telegram_ids.split(",") if uid.strip().isdigit()]

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(__file__), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

config = Settings()
