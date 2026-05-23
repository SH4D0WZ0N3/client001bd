# app/services/bootstrap.py
from pyrogram import Client
from loguru import logger
from app.utils.config import settings
from app.database.repositories import state_repo
from app.services.queue_manager import queue_manager

async def initial_channel_scan(client: Client):
    """
    Scans the source channel from a starting message ID and queues up all messages.
    This runs only if the state indicates it's the first run.
    """
    state = await state_repo.get_state()
    if state and state.last_processed_message_id > settings.START_MESSAGE_ID:
        logger.info("Initial scan already completed. Skipping.")
        return

    start_id = settings.START_MESSAGE_ID
    logger.info(f"Performing initial channel scan from message ID: {start_id}...")

    last_id = 0
    try:
        async for message in client.get_chat_history(settings.SOURCE_CHANNEL_ID, offset_id=start_id, reverse=True):
            if message.service: # Skip service messages like 'user joined'
                continue
            
            await queue_manager.add_message_to_queue(message)
            last_id = message.id
            if last_id % 100 == 0:
                logger.info(f"Scanned up to message ID: {last_id}")

        if last_id > 0:
            await state_repo.update_state(last_processed_id=last_id)
            logger.success(f"Initial scan complete. Last processed message ID: {last_id}")
        else:
            logger.warning("Initial scan finished, but no new messages were found to queue.")

    except Exception as e:
        logger.error(f"An error occurred during initial channel scan: {e}", exc_info=True)