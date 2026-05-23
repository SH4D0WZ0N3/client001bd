"""
Bootstrap — historical channel scan.

Scans from the LATEST message in the source channel backwards and stops
when it reaches a message with ID < START_MESSAGE_ID.  Messages are then
sorted ascending (oldest first) and inserted into the queue so they are
sent in the correct chronological order.

Requires USER_SESSION_STRING (a userbot session, not the bot token) because
bots cannot call get_chat_history on channels they are a member of via
the Bot API — only user accounts can read channel history.
"""

from pyrogram import Client
from loguru import logger
from app.utils.config import settings
from app.database.repositories import state_repo
from app.services.queue_manager import queue_manager

# Number of messages to insert before emitting a progress log
_PROGRESS_LOG_INTERVAL = 100


async def _run_historical_scan_with_userbot() -> None:
    logger.info("Starting historical scan using userbot session…")

    userbot = Client(
        name="userbot_scanner",
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        session_string=settings.USER_SESSION_STRING,
        in_memory=True,
    )

    try:
        await userbot.start()
        logger.info("Userbot client started for historical scan.")

        try:
            chat = await userbot.get_chat(settings.SOURCE_CHANNEL_ID)
            logger.info(
                f"Source channel resolved via userbot: '{chat.title}' "
                f"(id={chat.id})"
            )
        except Exception as exc:
            raise RuntimeError(
                f"Userbot cannot resolve source channel "
                f"{settings.SOURCE_CHANNEL_ID}: {exc}. "
                f"Ensure the user account is a member of that channel."
            )

        logger.info(
            f"Scanning channel history. Collecting messages with ID >= "
            f"{settings.START_MESSAGE_ID}…"
        )

        collected: list = []
        skipped = 0

        # FIX CF-2: offset_id=0 starts from the NEWEST message and iterates
        # backwards (newest → oldest).  We break as soon as we reach a message
        # older than START_MESSAGE_ID so we only queue content that is at or
        # after the configured starting point.
        #
        # Pyrogram get_chat_history(offset_id=N) returns messages OLDER than N.
        # offset_id=0 (default) means "start from the very latest message."
        async for message in userbot.get_chat_history(
            settings.SOURCE_CHANNEL_ID,
            offset_id=0,        # start from the latest, going backwards
        ):
            # Stop once we've gone past the desired starting point
            if message.id < settings.START_MESSAGE_ID:
                logger.info(
                    f"Reached message {message.id} < START_MESSAGE_ID "
                    f"({settings.START_MESSAGE_ID}). Stopping scan."
                )
                break

            if message.service:
                skipped += 1
                continue

            collected.append(message)

            if len(collected) % 200 == 0:
                logger.info(
                    f"Collected {len(collected)} messages so far "
                    f"(current ID={message.id})…"
                )

        if not collected:
            logger.warning(
                "Historical scan: no messages found in the configured range. "
                f"START_MESSAGE_ID={settings.START_MESSAGE_ID}"
            )
            return

        # Sort ascending (oldest first) so the queue preserves posting order
        collected.sort(key=lambda m: m.id)

        logger.info(
            f"Collected {len(collected)} messages "
            f"(skipped {skipped} service messages). "
            f"ID range: {collected[0].id} – {collected[-1].id}. "
            f"Inserting into queue…"
        )

        queued = 0
        for message in collected:
            await queue_manager.add_message_to_queue(message)
            queued += 1

            if queued % _PROGRESS_LOG_INTERVAL == 0:
                logger.info(
                    f"Queue progress: {queued}/{len(collected)}, "
                    f"current_id={message.id}"
                )

        last_id = collected[-1].id

        # FIX RC-1: Use $max (update_state_safe) so we never roll back the
        # cursor if live handlers already advanced it beyond last_id.
        await state_repo.update_state_safe(last_processed_id=last_id)

        logger.success(
            f"Historical scan complete. "
            f"queued={queued}, skipped={skipped}, last_id={last_id}"
        )

    finally:
        try:
            await userbot.stop()
            logger.info("Userbot client stopped.")
        except Exception:
            pass


async def initial_channel_scan(client: Client) -> None:
    """
    Run the historical scan on first startup.

    The `client` parameter is kept for API compatibility but is not used —
    the scan uses a separate userbot client internally.
    """
    state = await state_repo.get_state()

    if (
        state is not None
        and state.last_processed_message_id > settings.START_MESSAGE_ID
    ):
        logger.info(
            f"Historical scan already completed "
            f"(last_processed_message_id={state.last_processed_message_id}). "
            f"Skipping."
        )
        return

    if not settings.USER_SESSION_STRING:
        logger.warning(
            "USER_SESSION_STRING not set. Historical scan skipped. "
            "Only new messages arriving after bot startup will be queued."
        )
        if state is None:
            await state_repo.update_state_safe(
                last_processed_id=settings.START_MESSAGE_ID
            )
        return

    try:
        await _run_historical_scan_with_userbot()
    except Exception as exc:
        logger.error(
            f"Historical scan failed: {exc}. "
            f"Bot continues — live messages will still be queued normally.",
            exc_info=True,
        )
