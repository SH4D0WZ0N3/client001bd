from pyrogram import Client
from loguru import logger
from app.utils.config import settings
from app.database.repositories import state_repo
from app.services.queue_manager import queue_manager


async def initial_channel_scan(client: Client) -> None:
    """
    Scans the source channel forward from START_MESSAGE_ID and inserts every
    non-service message into the queue.

    Skipped entirely when state already records a processed message ID that
    is strictly greater than START_MESSAGE_ID, meaning a previous successful
    scan has already run.

    offset_id semantics in Pyrogram get_chat_history:
        offset_id=N  →  returns messages with id < N  (newer-to-older by default)
        reverse=True →  reverses the result to ascending order

    To start from START_MESSAGE_ID we set offset_id = START_MESSAGE_ID - 1 so
    that the first returned message has id >= START_MESSAGE_ID.
    """
    state = await state_repo.get_state()
    if state is not None and state.last_processed_message_id > settings.START_MESSAGE_ID:
        logger.info(
            f"Initial scan already completed "
            f"(last_processed_message_id={state.last_processed_message_id}). Skipping."
        )
        return

    offset = max(0, settings.START_MESSAGE_ID - 1)
    logger.info(
        f"Starting initial channel scan. "
        f"START_MESSAGE_ID={settings.START_MESSAGE_ID}, offset_id={offset}"
    )

    last_id = 0
    queued = 0
    skipped = 0

    try:
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
                logger.info(
                    f"Scan progress: {queued} queued, {skipped} skipped, "
                    f"last_id={last_id}"
                )

        if last_id > 0:
            await state_repo.update_state(last_processed_id=last_id)
            logger.success(
                f"Initial scan complete. "
                f"queued={queued}, skipped={skipped}, last_id={last_id}"
            )
        else:
            logger.warning("Initial scan found no messages to queue.")

    except Exception as exc:
        logger.error(f"Initial channel scan failed: {exc}", exc_info=True)
        raise
