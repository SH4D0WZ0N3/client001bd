"""
Application settings loaded from environment variables.

Sensitive values (BOT_TOKEN, API_HASH, USER_SESSION_STRING) are typed as
SecretStr so they are masked in tracebacks, debug prints, and Pydantic
serialization output.

To read the actual value in code: settings.BOT_TOKEN.get_secret_value()
"""
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # Telegram credentials
    API_ID: int
    API_HASH: SecretStr         # masked in logs
    BOT_TOKEN: SecretStr        # masked in logs

    # Userbot session string — generate once with generate_session.py
    USER_SESSION_STRING: Optional[SecretStr] = None

    # MongoDB
    MONGO_URI: str

    # Channels
    SOURCE_CHANNEL_ID: int
    TARGET_CHAT_ID: int
    PUBLIC_CHANNEL_LINK: str

    # Content
    FIXED_CAPTION: str = ""

    # Watermark text drawn on photos.
    # Leave empty ("") to disable watermarking entirely.
    # WATERMARK_TEXT was removed — use WATERMARK only to avoid confusion.
    WATERMARK: str = ""

    # Watermark appearance
    WATERMARK_COUNT: int = 4
    WATERMARK_OPACITY: int = 30
    WATERMARK_ROTATION: int = -35
    WATERMARK_FONT_SCALE: float = 0.04

    # Scheduling
    DAILY_LIMIT: int
    SEND_INTERVAL_SECONDS: int
    START_MESSAGE_ID: int = 1
    TIMEZONE: str = "UTC"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # Convenience accessors so call sites don't need .get_secret_value()
    # everywhere for the values that are used internally (not logged).
    @property
    def api_hash(self) -> str:
        return self.API_HASH.get_secret_value()

    @property
    def bot_token(self) -> str:
        return self.BOT_TOKEN.get_secret_value()

    @property
    def user_session_string(self) -> Optional[str]:
        return (
            self.USER_SESSION_STRING.get_secret_value()
            if self.USER_SESSION_STRING
            else None
        )


settings = Settings()
