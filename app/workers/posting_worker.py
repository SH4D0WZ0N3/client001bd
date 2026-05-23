from datetime import date
from loguru import logger
from pyrogram.errors import FloodWait
from app.utils.config import settings
from app.database.repositories import queue_repo, state_repo
from app.services.telegram_sender import TelegramSender


async def posting_job(sender: TelegramSender) -> None:
    """
    Scheduled job. Dequeues one pending item and sends it.

    Lifecycle:
        1. Reset daily counter when the calendar day rolls over.
        2. Enforce DAILY_LIMIT.
        3. Atomically dequeue the oldest pending item (find_one_and_update).
        4. Send via TelegramSender.
        5. Mark sent / failed in DB and update the daily counter.

    FloodWait:
        Re-queued as pending. The scheduler's natural interval provides the
        backoff gap. The item will be picked up on the next tick.
    """
    logger.debug("Posting worker tick started.")

    # ------------------------------------------------------------------ #
    # 1. Daily counter reset                                               #
    # ------------------------------------------------------------------ #
    today_str = date.today().isoformat()
    state = await state_repo.get_state()

    if state is None or state.last_reset_date != today_str:
        logger.info(f"New day ({today_str}). Resetting daily sent counter.")
        await state_repo.reset_daily_counter()
        # Re-fetch after upsert so count is accurate
        state = await state_repo.get_state()

    # Guard: state should always exist after reset_daily_counter (upsert=True),
    # but protect against an edge-case read failure.
    if state is None:
        logger.error("State document missing after reset. Skipping tick.")
        return

    # ------------------------------------------------------------------ #
    # 2. Daily limit check                                                 #
    # ------------------------------------------------------------------ #
    if state.daily_sent_count >= settings.DAILY_LIMIT:
        logger.info(
            f"Daily limit reached ({state.daily_sent_count}/{settings.DAILY_LIMIT}). "
            "Waiting for midnight reset."
        )
        return

    # ------------------------------------------------------------------ #
    # 3. Dequeue                                                           #
    # ------------------------------------------------------------------ #
    item = await queue_repo.get_next_pending_item()
    if item is None:
        logger.debug("Queue empty. Nothing to post.")
        return

    logger.info(f"Dequeued item: source_message_id={item.message_id} id={item.id}")

    # ------------------------------------------------------------------ #
    # 4 & 5. Send and update status                                        #
    # ------------------------------------------------------------------ #
    try:
        success = await sender.send_item(item)

        if success:
            await queue_repo.update_item_status(item.id, "sent")
            await state_repo.increment_daily_sent_count()
            logger.info(
                f"Item {item.id} sent. "
                f"Daily count: {state.daily_sent_count + 1}/{settings.DAILY_LIMIT}"
            )
        else:
            # Permanent failure — deleted message, invalid peer, etc.
            await queue_repo.update_item_status(
                item.id, "failed", "send_item() returned False"
            )
            logger.warning(f"Item {item.id} marked as failed (permanent).")

    except FloodWait as exc:
        logger.warning(
            f"FloodWait {exc.value}s for item {item.id}. Re-queuing as pending."
        )
        await queue_repo.update_item_status(item.id, "pending")

    except Exception as exc:
        logger.error(
            f"Unexpected error processing item {item.id}: {exc}", exc_info=True
        )
        await queue_repo.update_item_status(item.id, "failed", str(exc))

    logger.debug("Posting worker tick complete.")
