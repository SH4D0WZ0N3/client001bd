from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from pytz import timezone
from loguru import logger
from app.utils.config import settings
from app.workers.posting_worker import posting_job
from app.services.telegram_sender import TelegramSender


def setup_scheduler(
    sender: TelegramSender,
    immediate: bool = True,
) -> AsyncIOScheduler:
    logger.info("Setting up APScheduler…")

    jobstores = {"default": MemoryJobStore()}
    executors = {"default": AsyncIOExecutor()}
    tz = timezone(settings.TIMEZONE)

    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        executors=executors,
        timezone=tz,
    )

    now = datetime.now(tz=tz)

    if immediate:
        # Peers are confirmed ready — fire immediately
        first_run = now
        logger.info("First scheduler tick: immediate (peers resolved).")
    else:
        # Peers not confirmed — delay first tick to give more warmup time
        first_run = now + timedelta(seconds=60)
        logger.warning(
            "First scheduler tick delayed 60s (peer warmup timed out). "
            "Sends will retry automatically once peers resolve."
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
        next_run_time=first_run,
    )

    scheduler.start()
    logger.info(
        f"Scheduler started. Interval: {settings.SEND_INTERVAL_SECONDS}s. "
        f"Timezone: {settings.TIMEZONE}."
    )
    return scheduler