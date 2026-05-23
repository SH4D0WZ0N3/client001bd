# app/scheduler/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from pymongo import MongoClient
from loguru import logger
from pytz import timezone
from app.utils.config import settings
from app.workers.posting_worker import posting_job
from app.services.telegram_sender import TelegramSender

def setup_scheduler(sender: TelegramSender) -> AsyncIOScheduler:
    logger.info("Setting up scheduler...")

    # MongoDBJobStore requires a synchronous PyMongo client
    sync_client = MongoClient(settings.MONGO_URI)

    jobstores = {
        "default": MongoDBJobStore(
            database="apscheduler",
            collection="jobs",
            client=sync_client,
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
        "interval",
        seconds=settings.SEND_INTERVAL_SECONDS,
        args=[sender],
        id="posting_worker_job",
        replace_existing=True,
        max_instances=1,          # Prevent overlapping executions
        misfire_grace_time=120,
    )

    scheduler.start()
    logger.info(
        f"Scheduler started. Posting every {settings.SEND_INTERVAL_SECONDS}s."
    )
    return scheduler