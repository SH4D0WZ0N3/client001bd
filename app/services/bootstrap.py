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

    Pyrogram 2.x get_chat_history() does NOT support a `reverse` parameter.
    Messages are returned newest → oldest by default.

    Strategy:
        - Collect all messages into a list (newest→oldest from Pyrogram)
        - Sort ascending by message ID before queueing
        - This ensures queue insertion order matches source order, and that
          last_processed_message_id is set to the true highest ID seen.

    offset_id semantics in Pyrogram 2.x get_chat_history:
        offset_id=N  →  returns messages with id < N (i.e. older than N)
        offset_id=0  →  starts from the very latest message

    To include START_MESSAGE_ID itself we set offset_id = START_MESSAGE_ID + 1
    so that the first batch contains message IDs <= START_MESSAGE_ID (i.e. the
    target message is not excluded).

    Note: for very large channels (10k+ messages) consider chunked processing.
    For typical use-cases the full collect-and-sort approach is fine.
    """
    state = await state_repo.get_state()
    if state is not None and state.last_processed_message_id > settings.START_MESSAGE_ID:
        logger.info(
            f"Initial scan already completed "
            f"(last_processed_message_id={state.last_processed_message_id}). Skipping."
        )
        return

    # offset_id=N means "fetch messages older than N".
    # Setting it to START_MESSAGE_ID + 1 ensures the message at START_MESSAGE_ID
    # is included in the first batch returned.
    offset = settings.START_MESSAGE_ID + 1

    logger.info(
        f"Starting initial channel scan. "
        f"START_MESSAGE_ID={settings.START_MESSAGE_ID}, offset_id={offset}"
    )

    collected: list = []
    skipped = 0

    try:
        async for message in client.get_chat_history(
            settings.SOURCE_CHANNEL_ID,
            offset_id=offset,
        ):
            if message.service:
                skipped += 1
                continue
            collected.append(message)

        if not collected:
            logger.warning("Initial scan found no messages to queue.")
            return

        # Sort ascending so queue insertion order matches source chronology.
        # The queue dequeues by message_id ASC anyway, but correct order here
        # makes last_processed_message_id tracking unambiguous.
        collected.sort(key=lambda m: m.id)

        logger.info(
            f"Collected {len(collected)} messages (skipped {skipped} service). "
            f"ID range: {collected[0].id} – {collected[-1].id}. Queueing…"
        )

        queued = 0
        for message in collected:
            await queue_manager.add_message_to_queue(message)
            queued += 1

            if queued % 100 == 0:
                logger.info(
                    f"Scan progress: {queued}/{len(collected)} queued, "
                    f"current_id={message.id}"
                )

        last_id = collected[-1].id
        await state_repo.update_state(last_processed_id=last_id)

        logger.success(
            f"Initial scan complete. "
            f"queued={queued}, skipped={skipped}, last_id={last_id}"
        )

    except Exception as exc:
        logger.error(f"Initial channel scan failed: {exc}", exc_info=True)
        raise