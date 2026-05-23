from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from pymongo import MongoClient
from pytz import timezone
from loguru import logger
from app.utils.config import settings
from app.workers.posting_worker import posting_job
from app.services.telegram_sender import TelegramSender


def setup_scheduler(sender: TelegramSender) -> AsyncIOScheduler:
    logger.info("Setting up APScheduler…")

    # MongoDBJobStore requires a *synchronous* PyMongo client.
    # Passing MONGO_URI directly (not as 'host=') handles full URI strings
    # including auth credentials and replica-set params correctly.
    sync_mongo = MongoClient(settings.MONGO_URI)

    jobstores = {
        "default": MongoDBJobStore(
            database="apscheduler",
            collection="jobs",
            client=sync_mongo,
        )
    }
    executors = {
        "default": AsyncIOExecutor()
    }

    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        executors=executors,
        timezone=timezone(settings.TIMEZONE),
    )

    scheduler.add_job(
        posting_job,
        trigger="interval",
        seconds=settings.SEND_INTERVAL_SECONDS,
        args=[sender],
        id="posting_worker_job",
        replace_existing=True,
        max_instances=1,         # Prevent overlapping executions
        misfire_grace_time=120,  # Allow up to 2-minute late start
    )

    scheduler.start()
    logger.info(
        f"Scheduler started. Posting interval: {settings.SEND_INTERVAL_SECONDS}s. "
        f"Timezone: {settings.TIMEZONE}."
    )
    return scheduler
