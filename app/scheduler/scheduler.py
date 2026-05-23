from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from pytz import timezone
from loguru import logger
from app.utils.config import settings
from app.workers.posting_worker import posting_job
from app.services.telegram_sender import TelegramSender


def setup_scheduler(sender: TelegramSender) -> AsyncIOScheduler:
    logger.info("Setting up APScheduler…")

    # MongoDBJobStore + AsyncIOExecutor are incompatible:
    # MongoDBJobStore pickles jobs on save; AsyncIOExecutor contains
    # non-picklable objects (_queue.SimpleQueue). This causes a fatal
    # crash at startup. Since the job is re-registered from code on every
    # startup (replace_existing=True), MongoDB persistence of the job
    # definition provides zero benefit. MemoryJobStore is correct here.
    jobstores = {
        "default": MemoryJobStore()
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
        max_instances=1,
        misfire_grace_time=120,
    )

    scheduler.start()
    logger.info(
        f"Scheduler started. Posting interval: {settings.SEND_INTERVAL_SECONDS}s. "
        f"Timezone: {settings.TIMEZONE}."
    )
    return scheduler