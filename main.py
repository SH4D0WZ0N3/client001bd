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


async def _resolve_peer_background(app) -> None:
    """
    Resolve both channel peers in the background without blocking startup.
    Retries indefinitely every 15 seconds until both peers are cached.
    Triggered by any incoming update from either channel.
    """
    logger.info("Background peer resolver started. Send a message in source/target channel to trigger resolution.")

    resolved = {"source": False, "target": False}
    attempt = 0

    while not (resolved["source"] and resolved["target"]):
        attempt += 1
        await asyncio.sleep(15)

        if not resolved["source"]:
            try:
                chat = await app.get_chat(settings.SOURCE_CHANNEL_ID)
                logger.info(
                    f"Source peer resolved: '{getattr(chat, 'title', chat.id)}' "
                    f"(id={chat.id})"
                )
                resolved["source"] = True
            except Exception:
                pass

        if not resolved["target"]:
            try:
                chat = await app.get_chat(settings.TARGET_CHAT_ID)
                logger.info(
                    f"Target peer resolved: '{getattr(chat, 'title', chat.id)}' "
                    f"(id={chat.id})"
                )
                resolved["target"] = True
            except Exception:
                pass

        if not (resolved["source"] and resolved["target"]) and attempt % 4 == 0:
            logger.warning(
                f"Peers still resolving after {attempt * 15}s "
                f"(source={resolved['source']}, target={resolved['target']}). "
                f"Send any message in source channel to trigger update."
            )

    logger.success("Both peers resolved. All sends will now succeed.")


async def main() -> None:
    setup_logging()

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_async_exception_handler)

    await connect_to_mongo()
    await ensure_indexes()
    await queue_repo.recover_stale_processing_items()
    await queue_repo.recover_send_failed_items()

    app = create_bot_instance()
    sender = TelegramSender(app)

    await app.start()
    logger.success("Bot client started.")

    # Bootstrap — non-blocking, errors are caught internally
    try:
        await initial_channel_scan(app)
    except Exception as exc:
        logger.error(f"Initial channel scan failed (bot continues): {exc}", exc_info=True)

    # Start scheduler immediately — PeerIdInvalid is handled by re-queue logic
    scheduler = setup_scheduler(sender)

    # Start background peer resolver — does not block anything
    asyncio.create_task(
        _resolve_peer_background(app),
        name="peer_resolver",
    )

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