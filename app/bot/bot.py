import os

from loguru import logger
from pyrogram import Client

from app.utils.config import settings  # FIX: was app.config — that path doesn't exist

_bot: Client | None = None


def get_bot() -> Client:
    if _bot is None:
        raise RuntimeError("Bot not initialised. Call create_bot_instance() first.")
    return _bot


def create_bot_instance() -> Client:
    """
    Build a Pyrogram Client using a BOT TOKEN.

    Sessions are persisted to SESSION_DIR so the peer cache survives restarts.
    The session file is named bot_session.session.
    """
    global _bot

    session_dir = settings.SESSION_DIR
    logger.info(f"Initializing Pyrogram Client (session dir: {session_dir})…")
    os.makedirs(session_dir, exist_ok=True)

    # FIX: settings.SESSION_NAME doesn't exist in config.
    # Use a hardcoded name — the session file will be: /app/sessions/bot_session.session
    session_path = os.path.join(session_dir, "bot_session")

    _bot = Client(
        name=session_path,
        api_id=settings.API_ID,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        sleep_threshold=60,
    )

    from app.handlers.message_handlers import register_message_handlers
    from app.handlers.command_handlers import register_command_handlers
    register_message_handlers(_bot)
    register_command_handlers(_bot)

    logger.info("Pyrogram Client initialized and handlers registered.")
    return _bot