"""
Posting worker — dequeues one item per scheduler tick and sends it.

FIX HR-1: Daily counter reset now compares dates using the configured
TIMEZONE rather than the server's UTC clock.  This ensures the counter
resets at midnight in the user's local timezone, matching the timezone
passed to APScheduler.

FIX HR-2: Atomic daily limit check-and-increment. The old read-then-
increment pattern allowed over-sending when the counter was reset mid-day
or when overlapping containers ran simultaneously. The new
try_increment_daily_sent_count() method performs a single atomic MongoDB
operation: it only increments if below the limit AND the date still
matches. This makes the limit unbeatable regardless of restart patterns.

FIX-PEER: PeerIdInvalid is caught and re-queued as "pending" rather than
"failed". However, if the same item fails with PeerIdInvalid more than
MAX_PEER_RETRIES times in a row, it is marked "failed" to prevent an
infinite loop that blocks all other items in the queue. The primary fix
(peer resolution at startup in main.py) prevents this from ever happening
in normal operation.
"""

import asyncio
from datetime import datetime

import pytz
from loguru import logger
from pyrogram.errors import FloodWait, PeerIdInvalid

from app.utils.config import settings
from app.database.repositories import queue_repo, state_repo
from app.services.telegram_sender import TelegramSender

# Maximum seconds to sleep for a FloodWait before re-queuing.
_MAX_FLOODWAIT_SLEEP = 300  # 5 minutes

# After this many consecutive PeerIdInvalid failures on the SAME item,
# mark it failed instead of re-queuing. This prevents a single unresolvable
# peer from blocking the entire queue indefinitely.
_MAX_PEER_RETRIES = 20


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
        # No need to re-read state here; try_increment handles the date check.

    # ── Dequeue ───────────────────────────────────────────────────────────────
    item = await queue_repo.get_next_pending_item()
    if item is None:
        logger.debug("Queue empty. Nothing to post.")
        return

    logger.info(f"Dequeued item: source_message_id={item.message_id} id={item.id}")

    # ── Atomic daily limit check-and-increment ────────────────────────────────
    # Single MongoDB op: increments only if daily_sent_count < limit AND
    # last_reset_date matches today. Cannot be beaten by concurrent restarts.
    allowed = await state_repo.try_increment_daily_sent_count(
        today_str, settings.DAILY_LIMIT
    )
    if not allowed:
        await queue_repo.update_item_status(item.id, "pending")
        logger.info(
            f"Daily limit reached ({settings.DAILY_LIMIT}). "
            f"Item {item.id} re-queued as pending."
        )
        return

    # ── Send and update status ────────────────────────────────────────────────
    try:
        success = await sender.send_item(item)

        if success:
            await queue_repo.update_item_status(item.id, "sent")
            logger.info(f"Item {item.id} sent successfully.")
        else:
            # Undo the increment we already applied — send did not happen.
            await state_repo.decrement_daily_sent_count()
            await queue_repo.update_item_status(
                item.id, "failed", "send_item() returned False"
            )
            logger.warning(f"Item {item.id} marked as failed (send_item returned False).")

    except FloodWait as exc:
        wait_seconds = exc.value
        sleep_for = min(wait_seconds, _MAX_FLOODWAIT_SLEEP)
        logger.warning(
            f"FloodWait {wait_seconds}s for item {item.id}. "
            f"Sleeping {sleep_for}s then re-queuing as pending."
        )
        # Undo increment — this item is going back to pending.
        await state_repo.decrement_daily_sent_count()
        await asyncio.sleep(sleep_for)
        await queue_repo.update_item_status(item.id, "pending")
        logger.info(f"Item {item.id} re-queued as pending after FloodWait.")

    except PeerIdInvalid as exc:
        # Undo increment — this item is going back to pending.
        await state_repo.decrement_daily_sent_count()

        # Guard against infinite loops: if this item has already failed
        # PeerIdInvalid too many times, give up on it so the queue can advance.
        retry_count = getattr(item, "retry_count", 0) or 0
        if retry_count >= _MAX_PEER_RETRIES:
            logger.error(
                f"PeerIdInvalid for item {item.id} exceeded max retries "
                f"({_MAX_PEER_RETRIES}). Marking as failed to unblock queue. "
                f"Fix the peer issue then manually re-queue if needed. Error: {exc}"
            )
            await queue_repo.update_item_status(
                item.id, "failed",
                f"PeerIdInvalid after {_MAX_PEER_RETRIES} retries: {exc}"
            )
        else:
            logger.warning(
                f"PeerIdInvalid for item {item.id} (retry {retry_count + 1}/"
                f"{_MAX_PEER_RETRIES}): {exc}. Re-queuing as pending."
            )
            await queue_repo.update_item_status(item.id, "pending")

    except Exception as exc:
        err_str = str(exc).lower()

        # Fallback string check for Pyrogram builds where PeerIdInvalid
        # surfaces as a generic exception.
        if "peer id invalid" in err_str:
            await state_repo.decrement_daily_sent_count()

            retry_count = getattr(item, "retry_count", 0) or 0
            if retry_count >= _MAX_PEER_RETRIES:
                logger.error(
                    f"PeerIdInvalid (via Exception) for item {item.id} exceeded "
                    f"max retries ({_MAX_PEER_RETRIES}). Marking as failed. Error: {exc}"
                )
                await queue_repo.update_item_status(
                    item.id, "failed",
                    f"PeerIdInvalid after {_MAX_PEER_RETRIES} retries: {exc}"
                )
            else:
                logger.warning(
                    f"PeerIdInvalid (via Exception) for item {item.id} "
                    f"(retry {retry_count + 1}/{_MAX_PEER_RETRIES}): {exc}. "
                    "Re-queuing as pending."
                )
                await queue_repo.update_item_status(item.id, "pending")
            return

        # Genuine unexpected error — undo increment, mark failed.
        await state_repo.decrement_daily_sent_count()
        logger.error(
            f"Unexpected error processing item {item.id}: {exc}", exc_info=True
        )
        await queue_repo.update_item_status(item.id, "failed", str(exc))

    logger.debug("Posting worker tick complete.")