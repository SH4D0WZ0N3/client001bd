import asyncio
import signal
from loguru import logger
from app.utils.logging import setup_logging
from app.database.database import connect_to_mongo, close_mongo_connection, ensure_indexes
from app.bot import create_bot_instance
from app.services.telegram_sender import TelegramSender
from app.services.bootstrap import initial_channel_scan
from app.scheduler.scheduler import setup_scheduler


def _async_exception_handler(loop, context):
    msg = context.get("exception", context.get("message", "unknown"))
    logger.critical(f"Unhandled async exception: {msg}", exc_info=context.get("exception"))


async def main() -> None:
    setup_logging()

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_async_exception_handler)

    await connect_to_mongo()
    await ensure_indexes()

    app = create_bot_instance()
    sender = TelegramSender(app)

    await app.start()
    logger.success("Bot client started.")

    try:
        await initial_channel_scan(app)
    except Exception as exc:
        logger.error(
            f"Initial channel scan failed (bot continues running): {exc}",
            exc_info=True,
        )

    scheduler = setup_scheduler(sender)

    stop_event = asyncio.Event()

    def _handle_signal(sig):
        logger.warning(f"Signal {sig.name} received. Initiating shutdown...")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    logger.info("Bot is running. Waiting for stop signal...")

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        logger.warning("Main task cancelled.")
    finally:
        logger.info("Shutting down scheduler...")
        if scheduler.running:
            scheduler.shutdown(wait=False)

        logger.info("Stopping bot client...")
        await app.stop()

        logger.info("Closing database connection...")
        await close_mongo_connection()

        logger.success("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except BaseException as exc:
        logger.critical(f"Fatal error: {exc}", exc_info=True)