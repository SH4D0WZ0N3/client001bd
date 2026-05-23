# app/services/bootstrap.py
from pyrogram import Client
from loguru import logger
from app.utils.config import settings
from app.database.repositories import state_repo
from app.services.queue_manager import queue_manager

async def initial_channel_scan(client: Client):
    """
    Scans the source channel forward from START_MESSAGE_ID and queues everything.
    Skipped if a completed scan is already recorded in state.
    """
    state = await state_repo.get_state()
    if state and state.last_processed_message_id > settings.START_MESSAGE_ID:
        logger.info(
            f"Initial scan already done "
            f"(last processed: {state.last_processed_message_id}). Skipping."
        )
        return

    logger.info(f"Starting initial channel scan from message ID {settings.START_MESSAGE_ID}...")

    last_id = 0
    queued = 0
    skipped = 0

    try:
        # get_chat_history with offset_id=X and reverse=True fetches messages
        # with id >= X in ascending order. offset_id=0 means start from beginning.
        # To start from a specific ID, we use offset_id = START_MESSAGE_ID - 1
        # so the first returned message has id >= START_MESSAGE_ID.
        offset = max(0, settings.START_MESSAGE_ID - 1)

        async for message in client.get_chat_history(
            settings.SOURCE_CHANNEL_ID,
            offset_id=offset,
            reverse=True,
        ):
            if message.service:
                skipped += 1
                continue

            await queue_manager.add_message_to_queue(message)
            last_id = message.id
            queued += 1

            if queued % 100 == 0:
                logger.info(f"Scan progress: {queued} messages queued, last ID: {last_id}")

        if last_id > 0:
            await state_repo.update_state(last_processed_id=last_id)
            logger.success(
                f"Initial scan complete. Queued: {queued}, Skipped: {skipped}, "
                f"Last message ID: {last_id}"
            )
        else:
            logger.warning("Initial scan found no messages to queue.")

    except Exception as e:
        logger.error(f"Initial scan failed: {e}", exc_info=True)
        raise