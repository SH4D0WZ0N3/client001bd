from pyrogram import Client
from pyrogram.errors import PeerIdInvalid, ChannelInvalid
from loguru import logger
from app.utils.config import settings
from app.database.repositories import state_repo
from app.services.queue_manager import queue_manager


async def initial_channel_scan(client: Client) -> None:
    """
    Scans the source channel forward from START_MESSAGE_ID and inserts every
    non-service message into the queue.

    Skipped entirely when state already records a processed message ID that
    is strictly greater than START_MESSAGE_ID.

    Pyrogram 2.x get_chat_history() does NOT support a `reverse` parameter.
    Messages are returned newest → oldest. We collect, then sort ascending.

    Peer resolution: get_chat() must be called before get_chat_history() so
    that Pyrogram's internal peer cache is populated for this channel ID.
    Without this, any channel the bot hasn't interacted with yet raises
    PeerIdInvalid even with a correct numeric ID.
    """
    state = await state_repo.get_state()
    if state is not None and state.last_processed_message_id > settings.START_MESSAGE_ID:
        logger.info(
            f"Initial scan already completed "
            f"(last_processed_message_id={state.last_processed_message_id}). Skipping."
        )
        return

    # --- Peer resolution ---
    # Must be called before any history/message operations.
    # Populates Pyrogram's internal peer cache for this channel.
    try:
        chat = await client.get_chat(settings.SOURCE_CHANNEL_ID)
        logger.info(
            f"Source channel resolved: '{chat.title}' "
            f"(id={chat.id}, type={chat.type})"
        )
    except (PeerIdInvalid, ChannelInvalid) as exc:
        raise RuntimeError(
            f"Cannot resolve source channel {settings.SOURCE_CHANNEL_ID}. "
            f"Ensure the bot is an admin/member of that channel. Error: {exc}"
        )

    # offset_id=N means "fetch messages with id < N".
    # START_MESSAGE_ID + 1 ensures the message at START_MESSAGE_ID is included.
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