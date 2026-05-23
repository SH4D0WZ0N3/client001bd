import asyncio
from typing import Dict, List, Optional
from pyrogram.types import Message
from loguru import logger
from app.database.models import QueueItem
from app.database.repositories import queue_repo

_MEDIA_GROUP_FLUSH_DELAY: int = 3  # seconds to wait for lagging album messages


class QueueManager:
    def __init__(self) -> None:
        # Buffer: media_group_id → ordered list of Message objects
        self._buffer: Dict[str, List[Message]] = {}
        # Active flush tasks: media_group_id → asyncio.Task
        self._tasks: Dict[str, asyncio.Task] = {}
        # Per-group lock prevents interleaved buffer mutations
        self._group_locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add_message_to_queue(self, message: Message) -> None:
        if message.media_group_id:
            await self._handle_media_group(message)
        else:
            await self._handle_single(message)

    # ------------------------------------------------------------------
    # Single message
    # ------------------------------------------------------------------

    async def _handle_single(self, message: Message) -> None:
        logger.info(f"Queueing single message: {message.id}")
        item = QueueItem(message_id=message.id)
        await queue_repo.add_to_queue(item)

    # ------------------------------------------------------------------
    # Media group
    # ------------------------------------------------------------------

    def _lock_for(self, gid: str) -> asyncio.Lock:
        if gid not in self._group_locks:
            self._group_locks[gid] = asyncio.Lock()
        return self._group_locks[gid]

    async def _handle_media_group(self, message: Message) -> None:
        gid = message.media_group_id
        lock = self._lock_for(gid)

        async with lock:
            logger.debug(f"Buffering msg {message.id} → group {gid}")

            if gid not in self._buffer:
                self._buffer[gid] = []
            self._buffer[gid].append(message)

            # Cancel the existing flush timer and restart it.
            # This gives every incoming album message a fresh delay window.
            existing: Optional[asyncio.Task] = self._tasks.get(gid)
            if existing is not None and not existing.done():
                existing.cancel()
                try:
                    await existing
                except asyncio.CancelledError:
                    pass

            self._tasks[gid] = asyncio.create_task(
                self._flush_group(gid),
                name=f"flush_group_{gid}",
            )

    async def _flush_group(self, gid: str) -> None:
        """
        Waits for the coalesce delay then atomically moves the buffer to the DB
        queue. The DB unique index on message_id ensures restart-safety: if the
        process crashes after partial flush, re-inserted items are silently
        deduplicated.
        """
        await asyncio.sleep(_MEDIA_GROUP_FLUSH_DELAY)

        lock = self._lock_for(gid)
        async with lock:
            messages = self._buffer.pop(gid, [])
            self._tasks.pop(gid, None)
            self._group_locks.pop(gid, None)

        if not messages:
            logger.warning(f"Flush triggered for group {gid} but buffer was empty.")
            return

        messages.sort(key=lambda m: m.id)
        message_ids = [m.id for m in messages]
        first_id = message_ids[0]

        logger.info(
            f"Flushing media group {gid}: {len(message_ids)} messages, "
            f"IDs {message_ids[0]}–{message_ids[-1]}"
        )

        item = QueueItem(
            message_id=first_id,
            media_group_id=gid,
            message_ids=message_ids,
        )
        result = await queue_repo.add_to_queue(item)
        if result is None:
            logger.debug(f"Media group {gid} already in queue (deduplicated).")


queue_manager = QueueManager()
