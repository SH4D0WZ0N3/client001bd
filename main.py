# main.py
import asyncio
from loguru import logger
from app.utils.logging import setup_logging
from app.database.database import connect_to_mongo, close_mongo_connection, ensure_indexes
from app.bot import create_bot_instance
from app.services.telegram_sender import TelegramSender
from app.services.bootstrap import initial_channel_scan
from app.scheduler.scheduler import setup_scheduler

async def main():
    setup_logging()

    await connect_to_mongo()
    await ensure_indexes()

    app = create_bot_instance()
    sender = TelegramSender(app)

    await app.start()
    logger.success("Bot client started.")

    await initial_channel_scan(app)

    scheduler = setup_scheduler(sender)

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.warning("Shutdown signal received.")
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
    except Exception as e:
        logger.critical(f"Fatal startup error: {e}", exc_info=True)