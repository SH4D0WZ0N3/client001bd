# app/workers/posting_worker.py
from datetime import date
from loguru import logger
from pyrogram.errors import FloodWait
from app.utils.config import settings
from app.database.repositories import queue_repo, state_repo
from app.services.telegram_sender import TelegramSender

async def posting_job(sender: TelegramSender):
    logger.info("Posting worker: tick started.")

    # 1. Reset daily counter if it's a new day
    today_str = date.today().isoformat()
    state = await state_repo.get_state()

    if state is None or state.last_reset_date != today_str:
        logger.info(f"New day ({today_str}). Resetting daily counter.")
        await state_repo.reset_daily_counter()
        state = await state_repo.get_state()

    # 2. Guard against None state (first-ever run after reset)
    if state is None:
        logger.warning("State is None after reset — skipping this tick.")
        return

    # 3. Enforce daily limit
    if state.daily_sent_count >= settings.DAILY_LIMIT:
        logger.warning(
            f"Daily limit reached ({state.daily_sent_count}/{settings.DAILY_LIMIT}). "
            "Waiting for next reset."
        )
        return

    # 4. Dequeue next item
    item = await queue_repo.get_next_pending_item()
    if not item:
        logger.info("Queue empty. Nothing to post.")
        return

    logger.info(f"Processing queue item: source message ID {item.message_id}")

    # 5. Send
    try:
        success = await sender.send_item(item)
        if success:
            await queue_repo.update_item_status(item.id, "sent")
            await state_repo.increment_daily_sent_count()
        else:
            logger.error(f"Send returned False for item {item.id}. Marking failed.")
            await queue_repo.update_item_status(
                item.id, "failed", "send_item() returned False"
            )

    except FloodWait as e:
        logger.warning(
            f"FloodWait {e.value}s for item {item.id}. Re-queuing as pending."
        )
        # Reset to pending so it gets picked up next interval
        await queue_repo.update_item_status(item.id, "pending")

    except Exception as e:
        logger.error(f"Unexpected error for item {item.id}: {e}", exc_info=True)
        await queue_repo.update_item_status(item.id, "failed", str(e))

    logger.info("Posting worker: tick complete.")