from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram
    API_ID: int
    API_HASH: str
    BOT_TOKEN: str

    # Database
    MONGO_URI: str

    # Channels
    SOURCE_CHANNEL_ID: int
    TARGET_CHAT_ID: int
    PUBLIC_CHANNEL_LINK: str

    # Content
    FIXED_CAPTION: str = ""
    WATERMARK: str = ""

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
