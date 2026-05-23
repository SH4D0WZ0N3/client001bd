# app/workers/posting_worker.py
import asyncio
from datetime import date
from loguru import logger
from pyrogram.errors import FloodWait
from app.utils.config import settings
from app.database.repositories import queue_repo, state_repo
from app.services.telegram_sender import TelegramSender

async def posting_job(sender: TelegramSender):
    """The main job that gets an item from the queue and posts it."""
    logger.info("Posting worker job started.")

    # 1. Check and reset daily counter
    state = await state_repo.get_state()
    today_str = date.today().isoformat()

    if not state or state.last_reset_date != today_str:
        logger.info(f"New day ({today_str}). Resetting daily post counter.")
        await state_repo.reset_daily_counter()
        state = await state_repo.get_state()

    # 2. Check daily limit
    if state.daily_sent_count >= settings.DAILY_LIMIT:
        logger.warning(f"Daily post limit reached ({state.daily_sent_count}/{settings.DAILY_LIMIT}). Pausing until next reset.")
        return

    # 3. Get next item from queue
    item = await queue_repo.get_next_pending_item()
    if not item:
        logger.info("Queue is empty. Nothing to post.")
        return

    logger.info(f"Processing item from queue: Source Message ID {item.message_id}")

    # 4. Try to send the item
    try:
        success = await sender.send_item(item)
        if success:
            await queue_repo.update_item_status(item.id, "sent")
            await state_repo.increment_daily_sent_count()
        else:
            # Handle non-exception failures (e.g., message deleted)
            logger.error(f"Sending failed for item {item.id}. Marking as failed.")
            await queue_repo.update_item_status(item.id, "failed", "Sending process returned False")

    except FloodWait as e:
        # This is a special case. We want to pause and retry this specific item later.
        logger.warning(f"FloodWait for item {item.id}. Re-queueing as pending to retry later.")
        await queue_repo.update_item_status(item.id, "pending")
        # The scheduler's interval will naturally create a pause.
        # For a more aggressive retry, you could sleep here, but it's better to let the scheduler handle it.
    except Exception as e:
        logger.error(f"An unexpected error occurred while processing item {item.id}: {e}", exc_info=True)
        await queue_repo.update_item_status(item.id, "failed", str(e))

    logger.info("Posting worker job finished.")