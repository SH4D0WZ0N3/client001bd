# app/handlers/message_handlers.py
from pyrogram import Client, filters
from pyrogram.types import Message
from loguru import logger
from app.utils.config import settings
from app.services.queue_manager import queue_manager
from app.database.repositories import state_repo

def register_message_handlers(app: Client):
    @app.on_message(filters.chat(settings.SOURCE_CHANNEL_ID) & (filters.media_group | ~filters.service))
    async def new_channel_post(client: Client, message: Message):
        """
        Listens for new posts in the source channel and adds them to the queue.
        """
        logger.info(f"New message detected in source channel. ID: {message.id}")
        
        # Add to the processing queue
        await queue_manager.add_message_to_queue(message)
        
        # Update the last processed message ID state
        await state_repo.update_state(last_processed_id=message.id)