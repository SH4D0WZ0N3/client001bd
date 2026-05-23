# app/services/queue_manager.py
import asyncio
from typing import Dict
from pyrogram.types import Message
from loguru import logger
from app.database.models import QueueItem
from app.database.repositories import queue_repo

class QueueManager:
    def __init__(self):
        # In-memory buffer for media groups
        # Key: media_group_id, Value: list of message objects
        self.media_group_buffer: Dict[str, list[Message]] = {}
        # Key: media_group_id, Value: asyncio.Task
        self.media_group_timers: Dict[str, asyncio.Task] = {}

    async def add_message_to_queue(self, message: Message):
        """
        Adds a message to the processing queue.
        Handles media groups by buffering them.
        """
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
        logger.debug(f"Buffering message {message.id} for media group {media_group_id}")

        # Add message to buffer
        if media_group_id not in self.media_group_buffer:
            self.media_group_buffer[media_group_id] = []
        self.media_group_buffer[media_group_id].append(message)

        # If a timer is already running for this group, cancel it
        if media_group_id in self.media_group_timers:
            self.media_group_timers[media_group_id].cancel()

        # Start a new timer to process the group after a short delay
        self.media_group_timers[media_group_id] = asyncio.create_task(
            self._process_media_group_after_delay(media_group_id)
        )

    async def _process_media_group_after_delay(self, media_group_id: str, delay: int = 3):
        """Waits for a delay then processes the buffered media group."""
        await asyncio.sleep(delay)
        
        messages = self.media_group_buffer.pop(media_group_id, [])
        if not messages:
            return

        # Sort messages by ID to maintain order
        messages.sort(key=lambda m: m.id)
        
        message_ids = [m.id for m in messages]
        # Use the first message_id as the primary identifier for the queue item
        first_message_id = message_ids[0]

        logger.info(f"Queueing media group {media_group_id} with {len(message_ids)} items. First message ID: {first_message_id}")

        item = QueueItem(
            message_id=first_message_id,
            media_group_id=media_group_id,
            message_ids=message_ids
        )
        await queue_repo.add_to_queue(item)

        # Clean up timer task
        self.media_group_timers.pop(media_group_id, None)

queue_manager = QueueManager()