import asyncio
import signal
from loguru import logger
from app.utils.logging import setup_logging
from app.database.database import connect_to_mongo, close_mongo_connection, ensure_indexes
from app.database.repositories import queue_repo
from app.bot import create_bot_instance
from app.services.telegram_sender import TelegramSender
from app.services.bootstrap import initial_channel_scan
from app.services.queue_manager import queue_manager
from app.scheduler.scheduler import setup_scheduler
from app.utils.config import settings


def _async_exception_handler(loop, context):
    msg = context.get("exception", context.get("message", "unknown"))
    logger.critical(
        f"Unhandled async exception: {msg}",
        exc_info=context.get("exception"),
    )


async def _warm_up_peers(app) -> bool:
    """
    Wait for Pyrogram to sync pending updates from Telegram and populate
    the peer cache, then verify both channels are resolvable.

    Why this is needed:
    - On connect, Telegram sends pending updates asynchronously.
    - These updates contain access_hash values for channels the bot is in.
    - Until they are processed, any get_chat(numeric_id) raises ValueError.
    - get_dialogs() is blocked for bots (BOT_METHOD_INVALID).
    - Solution: wait for update processing, then retry peer resolution.

    Returns True if both peers resolved, False if timed out.
    """
    # Phase 1: Wait for Pyrogram's update sync to complete.
    # On a fresh connect, Telegram sends a GetDifference response that
    # includes all channel peer info. Pyrogram processes this asynchronously.
    # 20 seconds is generous but reliable even on slow Railway connections.
    logger.info("Waiting for Pyrogram update sync (20s)…")
    await asyncio.sleep(20)

    # Phase 2: Retry peer resolution until both channels are confirmed.
    _MAX_ATTEMPTS = 10
    _RETRY_DELAY = 10  # seconds

    source_ok = False
    target_ok = False

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        if not source_ok:
            try:
                chat = await app.get_chat(settings.SOURCE_CHANNEL_ID)
                logger.info(
                    f"Source peer resolved: '{getattr(chat, 'title', chat.id)}' "
                    f"(id={chat.id})"
                )
                source_ok = True
            except (ValueError, Exception) as exc:
                logger.warning(
                    f"Source peer not yet cached "
                    f"(attempt {attempt}/{_MAX_ATTEMPTS}): {exc}"
                )

        if not target_ok:
            try:
                chat = await app.get_chat(settings.TARGET_CHAT_ID)
                logger.info(
                    f"Target peer resolved: '{getattr(chat, 'title', chat.id)}' "
                    f"(id={chat.id})"
                )
                target_ok = True
            except (ValueError, Exception) as exc:
                logger.warning(
                    f"Target peer not yet cached "
                    f"(attempt {attempt}/{_MAX_ATTEMPTS}): {exc}"
                )

        if source_ok and target_ok:
            logger.info("Both peers resolved. Bot is ready to send.")
            return True

        if attempt < _MAX_ATTEMPTS:
            logger.info(
                f"Peers not ready yet "
                f"(source={source_ok}, target={target_ok}). "
                f"Retrying in {_RETRY_DELAY}s… "
                f"Send a message in the source channel to speed this up."
            )
            await asyncio.sleep(_RETRY_DELAY)

    logger.error(
        f"Peer warmup timed out after {_MAX_ATTEMPTS} attempts. "
        f"source={source_ok}, target={target_ok}. "
        f"VERIFY: (1) Bot is admin in BOTH channels. "
        f"(2) SOURCE_CHANNEL_ID={settings.SOURCE_CHANNEL_ID} is correct. "
        f"(3) TARGET_CHAT_ID={settings.TARGET_CHAT_ID} is correct. "
        f"Scheduler will start but sends may fail until peers resolve."
    )
    return False


async def main() -> None:
    setup_logging()

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_async_exception_handler)

    # ── Database ──────────────────────────────────────────────────────────────
    await connect_to_mongo()
    await ensure_indexes()
    await queue_repo.recover_stale_processing_items()
    await queue_repo.recover_send_failed_items()

    # ── Bot client ────────────────────────────────────────────────────────────
    app = create_bot_instance()
    sender = TelegramSender(app)

    await app.start()
    logger.success("Bot client started.")

    # Wait for peer cache to populate before starting the scheduler.
    # This is the primary fix for PeerIdInvalid on startup.
    peers_ready = await _warm_up_peers(app)

    # ── Bootstrap ─────────────────────────────────────────────────────────────
    try:
        await initial_channel_scan(app)
    except Exception as exc:
        logger.error(
            f"Initial channel scan failed (bot continues): {exc}",
            exc_info=True,
        )

    # ── Scheduler ─────────────────────────────────────────────────────────────
    # If peers resolved, fire first tick immediately.
    # If not, add a 60-second delay before first tick to give more time.
    scheduler = setup_scheduler(sender, immediate=peers_ready)

    # ── Signal handling ───────────────────────────────────────────────────────
    stop_event = asyncio.Event()

    def _handle_signal(sig):
        logger.warning(f"Signal {sig.name} received. Initiating shutdown…")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    logger.info("Bot is running. Waiting for stop signal…")

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        logger.warning("Main task cancelled.")
    finally:
        logger.info("Shutting down scheduler…")
        if scheduler.running:
            scheduler.shutdown(wait=False)

        logger.info("Draining pending album flush tasks…")
        await queue_manager.shutdown()

        logger.info("Stopping bot client…")
        await app.stop()

        logger.info("Closing database connection…")
        await close_mongo_connection()

        logger.success("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except BaseException as exc:
        logger.critical(f"Fatal error: {exc}", exc_info=True)