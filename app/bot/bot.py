import os

from loguru import logger
from pyrogram import Client

from app.config import settings

_bot: Client | None = None


def get_bot() -> Client:
    if _bot is None:
        raise RuntimeError("Bot not initialised. Call create_bot_instance() first.")
    return _bot


def create_bot_instance() -> Client:
    """
    Build a Pyrogram Client using a BOT TOKEN.

    Sessions are persisted to SESSION_DIR so the peer cache survives restarts.
    The session file is named <SESSION_NAME>.session.
    """
    global _bot

    logger.info(f"Initializing Pyrogram Client (session dir: {settings.SESSION_DIR})…")
    os.makedirs(settings.SESSION_DIR, exist_ok=True)

    _bot = Client(
        name=os.path.join(settings.SESSION_DIR, settings.SESSION_NAME),
        bot_token=settings.BOT_TOKEN,
        # Keep the connection alive; Pyrogram auto-reconnects on drop
        sleep_threshold=60,
    )

    from app.handlers.message_handlers import register_handlers
    register_handlers(_bot)

    logger.info("Pyrogram Client initialized and handlers registered.")
    return _bot