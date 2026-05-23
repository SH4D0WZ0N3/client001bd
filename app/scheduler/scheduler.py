# app/scheduler/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from loguru import logger
from pytz import timezone
from app.utils.config import settings
from app.workers.posting_worker import posting_job
from app.services.telegram_sender import TelegramSender

def setup_scheduler(sender: TelegramSender):
    """
    Configures and starts the APScheduler.
    """
    logger.info("Setting up scheduler...")
    
    jobstores = {
        'default': MongoDBJobStore(database="telegram_premium_bot_jobs", collection="apscheduler_jobs", host=settings.MONGO_URI)
    }
    executors = {
        'default': AsyncIOExecutor()
    }
    
    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        executors=executors,
        timezone=timezone(settings.TIMEZONE)
    )

    # Add the main posting job
    scheduler.add_job(
        posting_job,
        'interval',
        seconds=settings.SEND_INTERVAL_SECONDS,
        args=[sender],
        id='posting_worker_job',
        replace_existing=True,
        misfire_grace_time=60 # Allow 60s delay if scheduler is busy
    )

    scheduler.start()
    logger.info(f"Scheduler started. Posting job will run every {settings.SEND_INTERVAL_SECONDS} seconds.")
    return scheduler