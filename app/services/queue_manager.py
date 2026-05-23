"""
QueueManager — thread-safe media group coalescing.

Design: Replace the lock+cancel pattern (which causes deadlocks under rapid
album delivery) with a version-counter approach:

  * Each group gets a monotonically increasing version number.
  * The flush task captures its version at creation time.
  * When the task wakes up, it checks if its version is still current.
  * If a newer message arrived, the version will have advanced and the
    task exits silently — no cancellation, no lock contention.
  * Only the task whose version matches the current one proceeds to flush.
  * A single asyncio.Lock per group serialises the final pop+insert, but
    the lock is never held across an await-that-can-be-cancelled.
"""

import asyncio
from typing import Dict, List, Optional
from pyrogram.types import Message
from loguru import logger
from app.database.models import QueueItem
from app.database.repositories import queue_repo

_MEDIA_GROUP_FLUSH_DELAY: int = 3  # seconds


class _GroupState:
    __slots__ = ("messages", "version", "lock")

    def __init__(self) -> None:
        self.messages: List[Message] = []
        self.version: int = 0
        self.lock: asyncio.Lock = asyncio.Lock()


class QueueManager:
    def __init__(self) -> None:
        self._groups: Dict[str, _GroupState] = {}

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

    async def _handle_media_group(self, message: Message) -> None:
        gid = message.media_group_id

        # Create group state on first message, reuse on subsequent ones.
        # Dict access is atomic in CPython (GIL), and asyncio is single-
        # threaded, so no race here.
        if gid not in self._groups:
            self._groups[gid] = _GroupState()

        state = self._groups[gid]

        async with state.lock:
            state.messages.append(message)
            state.version += 1
            my_version = state.version
            logger.debug(
                f"Buffered msg {message.id} → group {gid} "
                f"(version={my_version}, total={len(state.messages)})"
            )

        # Spawn a flush task that will fire after the delay.
        # If more messages arrive before it fires, their tasks will see
        # a higher version and exit without doing anything.
        asyncio.create_task(
            self._flush_group(gid, my_version),
            name=f"flush_{gid}_v{my_version}",
        )

    async def _flush_group(self, gid: str, my_version: int) -> None:
        await asyncio.sleep(_MEDIA_GROUP_FLUSH_DELAY)

        if gid not in self._groups:
            return  # group was already flushed and cleaned up

        state = self._groups[gid]

        async with state.lock:
            # If our version is no longer current, a newer message arrived
            # after us — that task will handle the flush. Exit cleanly.
            if state.version != my_version:
                logger.debug(
                    f"Flush v{my_version} for group {gid} superseded by "
                    f"v{state.version}. Exiting."
                )
                return

            # We are the authoritative flush. Claim the messages.
            messages = list(state.messages)
            # Clean up group state INSIDE the lock so no new messages can
            # race into the old state after we pop it.
            del self._groups[gid]

        if not messages:
            logger.warning(f"Flush v{my_version} for group {gid}: buffer empty.")
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