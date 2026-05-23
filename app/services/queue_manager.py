# app/services/queue_manager.py
import asyncio
from typing import Dict, List
from pyrogram.types import Message
from loguru import logger
from app.database.models import QueueItem
from app.database.repositories import queue_repo

class QueueManager:
    def __init__(self):
        self._buffer: Dict[str, List[Message]] = {}
        self._timers: Dict[str, asyncio.Task] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, media_group_id: str) -> asyncio.Lock:
        if media_group_id not in self._locks:
            self._locks[media_group_id] = asyncio.Lock()
        return self._locks[media_group_id]

    async def add_message_to_queue(self, message: Message):
        if message.media_group_id:
            await self._handle_media_group_message(message)
        else:
            await self._handle_single_message(message)

    async def _handle_single_message(self, message: Message):
        logger.info(f"Queueing single message: {message.id}")
        item = QueueItem(message_id=message.id)
        await queue_repo.add_to_queue(item)

    async def _handle_media_group_message(self, message: Message):
        media_group_id = message.media_group_id
        lock = self._get_lock(media_group_id)

        async with lock:
            logger.debug(f"Buffering message {message.id} for media group {media_group_id}")

            if media_group_id not in self._buffer:
                self._buffer[media_group_id] = []
            self._buffer[media_group_id].append(message)

            # Cancel existing flush timer and restart it
            existing_task = self._timers.get(media_group_id)
            if existing_task and not existing_task.done():
                existing_task.cancel()
                try:
                    await existing_task
                except asyncio.CancelledError:
                    pass

            self._timers[media_group_id] = asyncio.create_task(
                self._flush_media_group(media_group_id, delay=3)
            )

    async def _flush_media_group(self, media_group_id: str, delay: int = 3):
        """Wait for more messages, then flush the buffer to the DB queue."""
        await asyncio.sleep(delay)

        lock = self._get_lock(media_group_id)
        async with lock:
            messages = self._buffer.pop(media_group_id, [])
            self._timers.pop(media_group_id, None)
            self._locks.pop(media_group_id, None)

        if not messages:
            logger.warning(f"Media group {media_group_id} flushed but buffer was empty.")
            return

        messages.sort(key=lambda m: m.id)
        message_ids = [m.id for m in messages]
        first_message_id = message_ids[0]

        logger.info(
            f"Flushing media group {media_group_id}: "
            f"{len(message_ids)} messages, first ID: {first_message_id}"
        )

        item = QueueItem(
            message_id=first_message_id,
            media_group_id=media_group_id,
            message_ids=message_ids,
        )
        await queue_repo.add_to_queue(item)

queue_manager = QueueManager()