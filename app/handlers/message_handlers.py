from pyrogram import Client, filters
from pyrogram.types import Message
from loguru import logger
from app.utils.config import settings
from app.services.queue_manager import queue_manager
from app.database.repositories import state_repo


def register_message_handlers(app: Client) -> None:
    @app.on_message(
        filters.chat(settings.SOURCE_CHANNEL_ID) & ~filters.service
    )
    async def new_channel_post(client: Client, message: Message) -> None:
        logger.info(
            f"Received message {message.id} from source channel "
            f"(media_group_id={message.media_group_id!r})"
        )
        await queue_manager.add_message_to_queue(message)
        # Use $max to ensure the cursor only moves forward even if bootstrap
        # is still running concurrently.
        await state_repo.update_state_safe(last_processed_id=message.id)