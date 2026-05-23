"""
Posting worker — dequeues one item per scheduler tick and sends it.

FIX HR-1: Daily counter reset now compares dates using the configured
TIMEZONE rather than the server's UTC clock.  This ensures the counter
resets at midnight in the user's local timezone, matching the timezone
passed to APScheduler.
"""

import asyncio
from datetime import datetime

import pytz
from loguru import logger
from pyrogram.errors import FloodWait

from app.utils.config import settings
from app.database.repositories import queue_repo, state_repo
from app.services.telegram_sender import TelegramSender

# Maximum seconds to sleep for a FloodWait before re-queuing.
# Prevents the scheduler slot from being held indefinitely on very long waits.
_MAX_FLOODWAIT_SLEEP = 300  # 5 minutes


def _today_str() -> str:
    """Return today's date string in the configured timezone, e.g. '2024-01-15'."""
    tz = pytz.timezone(settings.TIMEZONE)
    return datetime.now(tz=tz).date().isoformat()


async def posting_job(sender: TelegramSender) -> None:
    """
    Scheduled job. Dequeues one pending item and sends it to TARGET_CHAT_ID.
    """
    logger.debug("Posting worker tick started.")

    # ── Daily counter reset ───────────────────────────────────────────────────
    today_str = _today_str()
    state = await state_repo.get_state()

    if state is None or state.last_reset_date != today_str:
        logger.info(f"New day ({today_str}). Resetting daily sent counter.")
        await state_repo.reset_daily_counter()
        state = await state_repo.get_state()

    if state is None:
        logger.error("State document missing after reset. Skipping tick.")
        return

    # ── Daily limit check ─────────────────────────────────────────────────────
    if state.daily_sent_count >= settings.DAILY_LIMIT:
        logger.info(
            f"Daily limit reached ({state.daily_sent_count}/{settings.DAILY_LIMIT}). "
            "Waiting for midnight reset."
        )
        return

    # ── Dequeue ───────────────────────────────────────────────────────────────
    item = await queue_repo.get_next_pending_item()
    if item is None:
        logger.debug("Queue empty. Nothing to post.")
        return

    logger.info(f"Dequeued item: source_message_id={item.message_id} id={item.id}")

    # ── Send and update status ────────────────────────────────────────────────
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
            await queue_repo.update_item_status(
                item.id, "failed", "send_item() returned False"
            )
            logger.warning(f"Item {item.id} marked as failed (permanent).")

    except FloodWait as exc:
        wait_seconds = exc.value
        sleep_for = min(wait_seconds, _MAX_FLOODWAIT_SLEEP)
        logger.warning(
            f"FloodWait {wait_seconds}s for item {item.id}. "
            f"Sleeping {sleep_for}s then re-queuing as pending."
        )
        # Sleep, then re-queue. If the bot shuts down during this sleep,
        # recover_stale_processing_items() on the next startup will reset
        # the item back to "pending" automatically.
        await asyncio.sleep(sleep_for)
        await queue_repo.update_item_status(item.id, "pending")
        logger.info(f"Item {item.id} re-queued as pending after FloodWait.")

    except Exception as exc:
        logger.error(
            f"Unexpected error processing item {item.id}: {exc}", exc_info=True
        )
        await queue_repo.update_item_status(item.id, "failed", str(exc))

    logger.debug("Posting worker tick complete.")
