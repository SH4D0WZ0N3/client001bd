"""
APScheduler setup.

The scheduler always fires the first tick immediately on startup
(next_run_time = now).  Peer resolution is handled in main.py BEFORE
this function is called, so by the time the first tick fires the peers
should already be cached.  If a peer is still unresolved, the posting
worker re-queues the item as pending automatically — no special delay
in the scheduler is needed or helpful.
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


def setup_scheduler(
    sender: TelegramSender,
    peers_ready: bool = True,
) -> AsyncIOScheduler:
    logger.info("Setting up APScheduler…")

    # MemoryJobStore is correct here.  MongoDBJobStore + AsyncIOExecutor
    # are incompatible at pickle time, and the job is re-registered from
    # code on every startup (replace_existing=True), so persistence adds
    # no value.
    jobstores = {"default": MemoryJobStore()}
    executors = {"default": AsyncIOExecutor()}
    tz = timezone(settings.TIMEZONE)

    scheduler = AsyncIOScheduler(
        jobstores=jobstores,
        executors=executors,
        timezone=tz,
    )

    # Always fire immediately.  If a peer is unresolved the first send will
    # fail with PeerIdInvalid, the item will be re-queued as pending, and
    # the next tick will retry.  No delayed start is needed — the retry
    # mechanism handles the transient failure correctly.
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
        f"Scheduler started. "
        f"Interval: {settings.SEND_INTERVAL_SECONDS}s. "
        f"Timezone: {settings.TIMEZONE}. "
        f"Peers ready: {peers_ready}. "
        f"First tick: immediate."
    )
    return scheduler
