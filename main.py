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


async def _warm_up_peers(app) -> None:
    """
    Populate Pyrogram's peer cache before the scheduler fires.

    Root cause of PeerIdInvalid:
    Pyrogram stores channel access hashes in the session SQLite database.
    On a fresh session (or after session reset), the bot has NEVER sent to
    these channels, so no access hashes are cached.  get_chat(integer_id)
    cannot resolve a peer it has no access hash for — it's circular.

    The fix: get_dialogs() calls messages.GetDialogs via MTProto, which
    Telegram responds to with all channels/groups the bot is a member of,
    including their full peer info (access hashes).  Pyrogram automatically
    stores these in the session peer table, making all subsequent send calls
    work correctly.

    Fallback: if get_dialogs() fails for any reason, a 5-second sleep lets
    Pyrogram process the initial update diff that Telegram always sends on
    connect, which also includes channel peer info.
    """
    try:
        logger.info("Warming up peer cache via get_dialogs()…")
        count = 0
        async for _ in app.get_dialogs():
            count += 1
        logger.info(f"Peer cache populated: {count} dialog(s) loaded.")
    except Exception as exc:
        logger.warning(
            f"get_dialogs() failed ({exc}). "
            "Falling back to 5-second startup delay for update-based peer caching."
        )
        # Pyrogram's update handler processes pending updates on startup,
        # which also includes channel peer info.  Give it a few seconds.
        await asyncio.sleep(5)

    # Verify both channels are now resolvable
    for chat_id, label in [
        (settings.SOURCE_CHANNEL_ID, "source"),
        (settings.TARGET_CHAT_ID, "target"),
    ]:
        try:
            chat = await app.get_chat(chat_id)
            logger.info(
                f"{label.capitalize()} peer resolved: "
                f"'{getattr(chat, 'title', chat_id)}' (id={chat.id})"
            )
        except Exception as exc:
            # Non-fatal: posting_worker will re-queue on PeerIdInvalid.
            # Log as error so the operator knows to check channel permissions.
            logger.error(
                f"Could not resolve {label} peer (id={chat_id}): {exc}. "
                f"Check that the bot is an admin in that channel and the ID is correct."
            )


async def main() -> None:
    setup_logging()

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_async_exception_handler)

    # ── Database ──────────────────────────────────────────────────────────────
    await connect_to_mongo()
    await ensure_indexes()

    # Recover items stuck in "processing" from a previous crash
    await queue_repo.recover_stale_processing_items()

    # Recover items that were permanently-failed due to PeerIdInvalid
    # (marked with error_message="send_item() returned False").
    # After peer warm-up below these will send normally.
    await queue_repo.recover_send_failed_items()

    # ── Bot client ────────────────────────────────────────────────────────────
    app = create_bot_instance()
    sender = TelegramSender(app)

    await app.start()
    logger.success("Bot client started.")

    # CRITICAL: populate peer cache BEFORE the scheduler fires its first tick.
    # This is the primary fix for PeerIdInvalid on fresh/reset sessions.
    await _warm_up_peers(app)

    # ── Bootstrap (historical scan) ───────────────────────────────────────────
    try:
        await initial_channel_scan(app)
    except Exception as exc:
        logger.error(
            f"Initial channel scan failed (bot continues running): {exc}",
            exc_info=True,
        )

    # ── Scheduler ─────────────────────────────────────────────────────────────
    scheduler = setup_scheduler(sender)

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
        # ── Shutdown sequence ─────────────────────────────────────────────────
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