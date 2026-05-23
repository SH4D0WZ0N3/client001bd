"""
Bootstrap: initial channel scan.

Uses streaming insertion (no full-collection memory buffer) to handle
channels with tens-of-thousands of messages safely.

Calls update_state with $max semantics via the repository so a concurrent
live handler can never roll back the cursor.
"""

from pyrogram import Client
from pyrogram.errors import PeerIdInvalid, ChannelInvalid
from loguru import logger
from app.utils.config import settings
from app.database.repositories import state_repo
from app.services.queue_manager import queue_manager

_BATCH_LOG_INTERVAL = 100


async def initial_channel_scan(client: Client) -> None:
    """
    Scans the source channel and inserts every non-service message into
    the queue. Skipped if state already records progress beyond START_MESSAGE_ID.

    Streams messages in reverse (newest→oldest per Pyrogram default),
    collects into small batches for logging, inserts immediately — never
    buffers the entire channel history.
    """
    state = await state_repo.get_state()
    if state is not None and state.last_processed_message_id > settings.START_MESSAGE_ID:
        logger.info(
            f"Initial scan already completed "
            f"(last_processed_message_id={state.last_processed_message_id}). Skipping."
        )
        return

    # Peer resolution — populates Pyrogram's internal peer cache.
    try:
        chat = await client.get_chat(settings.SOURCE_CHANNEL_ID)
        logger.info(
            f"Source channel resolved: '{chat.title}' "
            f"(id={chat.id}, type={chat.type})"
        )
    except (PeerIdInvalid, ChannelInvalid) as exc:
        raise RuntimeError(
            f"Cannot resolve source channel {settings.SOURCE_CHANNEL_ID}. "
            f"Ensure the bot is an admin/member. Error: {exc}"
        )

    logger.info(
        f"Starting initial channel scan from START_MESSAGE_ID={settings.START_MESSAGE_ID}."
    )

    # Collect IDs in a first streaming pass to determine the range,
    # then insert in ascending order. We keep only IDs (ints) in memory
    # during the first pass — not full Message objects.
    # For very large channels (>50k), this still uses O(N) ints but that
    # is ~400KB for 50k IDs, which is acceptable.
    all_ids: list[int] = []
    skipped = 0

    try:
        async for message in client.get_chat_history(settings.SOURCE_CHANNEL_ID):
            if message.id < settings.START_MESSAGE_ID:
                break  # history is newest→oldest; stop when we pass the start boundary
            if message.service:
                skipped += 1
                continue
            all_ids.append(message.id)

        if not all_ids:
            logger.warning("Initial scan found no messages to queue.")
            return

        # Sort ascending to queue in chronological order.
        all_ids.sort()

        logger.info(
            f"Scan collected {len(all_ids)} message IDs "
            f"(skipped {skipped} service). "
            f"ID range: {all_ids[0]}–{all_ids[-1]}. Queueing…"
        )

        # Second pass: fetch and insert in ascending order.
        # We re-fetch in batches of 200 (Telegram API limit for get_messages).
        queued = 0
        batch_size = 200
        max_id = all_ids[-1]

        for i in range(0, len(all_ids), batch_size):
            batch_ids = all_ids[i : i + batch_size]
            messages = await client.get_messages(
                settings.SOURCE_CHANNEL_ID, batch_ids
            )
            # get_messages with a list returns a list
            if not isinstance(messages, list):
                messages = [messages]

            for msg in sorted(
                [m for m in messages if m and not m.empty and not m.service],
                key=lambda m: m.id,
            ):
                await queue_manager.add_message_to_queue(msg)
                queued += 1

                if queued % _BATCH_LOG_INTERVAL == 0:
                    logger.info(
                        f"Scan progress: {queued}/{len(all_ids)} queued "
                        f"(current_id={msg.id})"
                    )

        await state_repo.update_state_safe(last_processed_id=max_id)

        logger.success(
            f"Initial scan complete. queued={queued}, skipped={skipped}, "
            f"last_id={max_id}"
        )

    except Exception as exc:
        logger.error(f"Initial channel scan failed: {exc}", exc_info=True)
        raise