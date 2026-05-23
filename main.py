# main.py
import asyncio
from loguru import logger
from app.utils.logging import setup_logging
from app.database.database import connect_to_mongo, close_mongo_connection
from app.bot import create_bot_instance
from app.services.telegram_sender import TelegramSender
from app.services.bootstrap import initial_channel_scan
from app.scheduler.scheduler import setup_scheduler

async def main():
    # 1. Setup Logging
    setup_logging()

    # 2. Connect to Database
    await connect_to_mongo()

    # 3. Initialize Pyrogram Client
    app = create_bot_instance()
    
    # 4. Initialize Services
    sender = TelegramSender(app)

    # 5. Start the bot client
    await app.start()
    logger.success("Bot client started successfully.")

    # 6. Run initial bootstrap scan if needed
    await initial_channel_scan(app)

    # 7. Setup and start the scheduler
    scheduler = setup_scheduler(sender)

    # 8. Keep the application running
    try:
        while True:
            await asyncio.sleep(3600) # Keep the main coroutine alive
    except (KeyboardInterrupt, SystemExit):
        logger.warning("Shutdown signal received.")
    finally:
        # 9. Graceful shutdown
        logger.info("Shutting down scheduler...")
        if scheduler.running:
            scheduler.shutdown()
        
        logger.info("Stopping bot client...")
        await app.stop()
        
        logger.info("Closing database connection...")
        await close_mongo_connection()
        
        logger.success("Application shut down gracefully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"Application failed to start or crashed: {e}", exc_info=True)