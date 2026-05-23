from datetime import datetime
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

    jobstores = {"default": MemoryJobStore()}
    executors = {"default": AsyncIOExecutor()}
    tz = timezone(settings.TIMEZONE)

    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        executors=executors,
        timezone=tz,
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
        next_run_time=datetime.now(tz=tz),
    )

    scheduler.start()
    logger.info(
        f"Scheduler started. Interval: {settings.SEND_INTERVAL_SECONDS}s. "
        f"Timezone: {settings.TIMEZONE}. First tick: immediate."
    )
    return scheduler