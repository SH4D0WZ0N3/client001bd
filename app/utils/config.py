from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # Telegram
    API_ID: int
    API_HASH: str
    BOT_TOKEN: str

    # Userbot session string (optional — only needed for historical scan)
    # Generate once with generate_session.py, then set as env var on Railway.
    USER_SESSION_STRING: Optional[str] = None

    # Database
    MONGO_URI: str

    # Channels
    SOURCE_CHANNEL_ID: int
    TARGET_CHAT_ID: int
    PUBLIC_CHANNEL_LINK: str

    # Content
    FIXED_CAPTION: str = ""
    WATERMARK: str = ""

    # Watermark image settings
    WATERMARK_TEXT: str = "Watermark"
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


settings = Settings()