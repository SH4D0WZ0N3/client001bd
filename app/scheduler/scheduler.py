"""
APScheduler setup.

FIX HR-5: next_run_time is set to now() so the first posting tick fires
immediately on startup rather than waiting a full SEND_INTERVAL_SECONDS
after launch.  This means the bot starts posting as soon as it's ready
rather than sitting idle for up to 30 minutes after a restart.
"""

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

    # MongoDBJobStore + AsyncIOExecutor are incompatible at serialization time.
    # MemoryJobStore is correct here — the job is re-registered from code on
    # every startup (replace_existing=True), so persistence adds no value.
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
        # Fire the first tick immediately so posting resumes right after
        # startup instead of waiting a full interval.
        next_run_time=datetime.now(tz=tz),
    )

    scheduler.start()
    logger.info(
        f"Scheduler started. Posting interval: {settings.SEND_INTERVAL_SECONDS}s. "
        f"Timezone: {settings.TIMEZONE}. "
        f"First tick: immediate."
    )
    return scheduler
