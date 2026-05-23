# app/bot.py
from pyrogram import Client
from loguru import logger
from app.utils.config import settings
from app.handlers.command_handlers import register_command_handlers
from app.handlers.message_handlers import register_message_handlers

def create_bot_instance() -> Client:
    """
    Creates and configures the Pyrogram Client instance.
    """
    logger.info("Initializing Pyrogram Client...")
    
    app = Client(
        "premium_bot",
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        bot_token=settings.BOT_TOKEN
    )

    # Register all handlers
    register_command_handlers(app)
    register_message_handlers(app)
    
    logger.info("Pyrogram Client initialized and handlers registered.")
    return app