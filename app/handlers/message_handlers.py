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
        """
        Receives every non-service message from the source channel.
        Media groups are coalesced inside QueueManager via an in-memory buffer.
        Single messages are queued immediately.
        """
        logger.info(
            f"Received message {message.id} from source channel "
            f"(media_group_id={message.media_group_id!r})"
        )
        await queue_manager.add_message_to_queue(message)
        await state_repo.update_state(last_processed_id=message.id)
