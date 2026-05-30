import random
from datetime import datetime, date
from typing import List, Optional

from bson import ObjectId
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.database.database import get_db
from app.database.models import QueueItem, SentLog, State


class BaseRepository:
    def __init__(self, collection_name: str):
        self._collection_name = collection_name
        self._collection: Optional[AsyncIOMotorCollection] = None

    @property
    def collection(self) -> AsyncIOMotorCollection:
        if self._collection is None:
            self._collection = get_db()[self._collection_name]
        return self._collection


class QueueRepository(BaseRepository):
    def __init__(self):
        super().__init__("queue")

    async def add_to_queue(self, item: QueueItem) -> Optional[QueueItem]:
        try:
            result = await self.collection.insert_one(
                item.model_dump(by_alias=True, exclude={"id"})
            )
            item.id = result.inserted_id
            return item
        except DuplicateKeyError:
            logger.debug(f"Message {item.message_id} already in queue. Skipping.")
            return None

    async def get_next_pending_item(self) -> Optional[QueueItem]:
        raw = await self.collection.find_one_and_update(
            {"status": "pending"},
            {"$set": {"status": "processing", "scheduled_at": datetime.utcnow()}},
            sort=[("message_id", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            return None
        return QueueItem(**raw)

    async def recover_stale_processing_items(self) -> int:
        result = await self.collection.update_many(
            {"status": "processing"},
            {"$set": {"status": "pending", "scheduled_at": None}},
        )
        count = result.modified_count
        if count > 0:
            logger.warning(
                f"Recovered {count} item(s) stuck in 'processing' status "
                f"(likely from a previous crash)."
            )
        return count

    async def recover_send_failed_items(self, max_retry_count: int = 10) -> int:
        result = await self.collection.update_many(
            {
                "status": "failed",
                "error_message": "send_item() returned False",
                "retry_count": {"$lte": max_retry_count},
            },
            {
                "$set": {
                    "status": "pending",
                    "error_message": None,
                    "scheduled_at": None,
                }
            },
        )
        count = result.modified_count
        if count > 0:
            logger.info(
                f"Recovered {count} failed item(s) for retry "
                f"(were marked failed with 'send_item() returned False')."
            )
        return count

    async def update_item_status(
        self,
        item_id,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        object_id = (
            item_id if isinstance(item_id, ObjectId) else ObjectId(str(item_id))
        )

        set_fields: dict = {"status": status}

        if status == "sent":
            set_fields["sent_at"] = datetime.utcnow()
            set_fields["error_message"] = None

        if status == "pending":
            set_fields["scheduled_at"] = None
            set_fields["error_message"] = None

        if status == "failed" and error_message is not None:
            set_fields["error_message"] = error_message

        update_doc: dict = {"$set": set_fields}

        if status == "failed":
            update_doc["$inc"] = {"retry_count": 1}

        result = await self.collection.update_one({"_id": object_id}, update_doc)
        if result.matched_count == 0:
            logger.warning(
                f"update_item_status: no document matched _id={object_id} "
                f"(status={status})"
            )

    async def get_vault_item_ids(self) -> List[ObjectId]:
        """
        Return all _ids from the queue collection where status is 'sent' or 'failed'.
        These form the replay vault.
        """
        cursor = self.collection.find(
            {"status": {"$in": ["sent", "failed"]}},
            {"_id": 1},
        )
        docs = await cursor.to_list(length=None)
        return [doc["_id"] for doc in docs]

    async def get_item_by_id(self, oid: ObjectId) -> Optional[QueueItem]:
        """Fetch a single queue document by its _id (used during vault replay)."""
        doc = await self.collection.find_one({"_id": oid})
        if doc is None:
            return None
        return QueueItem(**doc)


class StateRepository(BaseRepository):
    def __init__(self):
        super().__init__("state")
        self.state_id = "main_state"

    async def get_state(self) -> Optional[State]:
        doc = await self.collection.find_one({"_id": self.state_id})
        return State(**doc) if doc else None

    async def update_state(self, last_processed_id: int) -> None:
        await self.collection.update_one(
            {"_id": self.state_id},
            {
                "$set": {"last_processed_message_id": last_processed_id},
                "$setOnInsert": {
                    "daily_sent_count": 0,
                    "last_reset_date": date.today().isoformat(),
                },
            },
            upsert=True,
        )

    async def update_state_safe(self, last_processed_id: int) -> None:
        await self.collection.update_one(
            {"_id": self.state_id},
            {
                "$max": {"last_processed_message_id": last_processed_id},
                "$setOnInsert": {
                    "daily_sent_count": 0,
                    "last_reset_date": date.today().isoformat(),
                },
            },
            upsert=True,
        )

    async def mark_scan_completed(self) -> None:
        await self.collection.update_one(
            {"_id": self.state_id},
            {
                "$set": {"scan_completed": True},
                "$setOnInsert": {
                    "daily_sent_count": 0,
                    "last_reset_date": date.today().isoformat(),
                    "last_processed_message_id": 0,
                },
            },
            upsert=True,
        )

    async def reset_daily_counter(self, today: Optional[str] = None) -> None:
        today_str = today if today is not None else date.today().isoformat()
        await self.collection.update_one(
            {"_id": self.state_id},
            {
                "$set": {"daily_sent_count": 0, "last_reset_date": today_str},
                "$setOnInsert": {"last_processed_message_id": 0},
            },
            upsert=True,
        )

    async def try_increment_daily_sent_count(self, today: str, limit: int) -> bool:
        result = await self.collection.find_one_and_update(
            {
                "_id": self.state_id,
                "last_reset_date": today,
                "$expr": {"$lt": ["$daily_sent_count", limit]},
            },
            {"$inc": {"daily_sent_count": 1}},
            return_document=ReturnDocument.AFTER,
        )
        return result is not None

    async def decrement_daily_sent_count(self) -> None:
        await self.collection.update_one(
            {"_id": self.state_id, "daily_sent_count": {"$gt": 0}},
            {"$inc": {"daily_sent_count": -1}},
        )

    async def increment_daily_sent_count(self) -> None:
        """Legacy unconditional increment — prefer try_increment_daily_sent_count."""
        await self.collection.update_one(
            {"_id": self.state_id},
            {"$inc": {"daily_sent_count": 1}},
            upsert=True,
        )


class SentLogRepository(BaseRepository):
    def __init__(self):
        super().__init__("sent_logs")

    async def create_log(self, log: SentLog) -> None:
        await self.collection.insert_one(
            log.model_dump(by_alias=True, exclude={"id"})
        )


class VaultReplayRepository(BaseRepository):
    """
    Manages the vault replay cycle state in the `vault_replay_state` collection.

    Single document schema:
    {
        _id: "replay_state",
        active: bool,
        cycle_number: int,
        remaining_ids: [ObjectId, ...],   # shuffled, not yet sent this cycle
        completed_ids: [ObjectId, ...],   # sent in current cycle
        last_updated: datetime
    }

    All mutations that combine a read and a write use find_one_and_update
    so they are atomic at the MongoDB document level.
    """

    _DOC_ID = "replay_state"

    def __init__(self):
        super().__init__("vault_replay_state")

    async def get_state(self) -> Optional[dict]:
        return await self.collection.find_one({"_id": self._DOC_ID})

    async def set_active(self, active: bool) -> None:
        await self.collection.update_one(
            {"_id": self._DOC_ID},
            {
                "$set": {
                    "active": active,
                    "last_updated": datetime.utcnow(),
                }
            },
            upsert=True,
        )

    async def reset_cycle(
        self, shuffled_ids: List[ObjectId], cycle_number: int
    ) -> None:
        """
        Start a fresh replay cycle. Replaces remaining_ids with a new shuffle,
        clears completed_ids, sets active=True.
        Upserts so it works even if the document never existed.
        """
        await self.collection.update_one(
            {"_id": self._DOC_ID},
            {
                "$set": {
                    "active": True,
                    "cycle_number": cycle_number,
                    "remaining_ids": shuffled_ids,
                    "completed_ids": [],
                    "last_updated": datetime.utcnow(),
                }
            },
            upsert=True,
        )

    async def pop_next_replay_id(self) -> Optional[ObjectId]:
        """
        Atomically pop the first ObjectId from remaining_ids and push it onto
        completed_ids. Returns the popped id, or None if remaining_ids is empty.

        Uses $pop + $push in a single find_one_and_update — the document
        is never in an inconsistent state from concurrent access.
        """
        # $pop with -1 removes the first element; we capture the doc BEFORE
        # the update so we can read which id was at position 0.
        doc = await self.collection.find_one_and_update(
            {
                "_id": self._DOC_ID,
                # Only match when there is at least one id left
                "remaining_ids.0": {"$exists": True},
            },
            [
                # Aggregation pipeline update — lets us reference the array
                # element we're removing in the same operation.
                {
                    "$set": {
                        "completed_ids": {
                            "$concatArrays": [
                                "$completed_ids",
                                [{"$arrayElemAt": ["$remaining_ids", 0]}],
                            ]
                        },
                        "remaining_ids": {"$slice": ["$remaining_ids", 1, {"$size": "$remaining_ids"}]},
                        "last_updated": "$$NOW",
                    }
                }
            ],
            # BEFORE gives us the doc with the id still in remaining_ids[0]
            return_document=ReturnDocument.BEFORE,
        )

        if doc is None:
            return None

        remaining = doc.get("remaining_ids", [])
        if not remaining:
            return None

        return remaining[0]

    async def push_back_id(self, oid: ObjectId) -> None:
        """
        Return a previously popped id to the FRONT of remaining_ids and
        remove it from completed_ids. Used for FloodWait recovery so the
        item is retried on the next tick.
        """
        await self.collection.update_one(
            {"_id": self._DOC_ID},
            {
                "$push": {"remaining_ids": {"$each": [oid], "$position": 0}},
                "$pull": {"completed_ids": oid},
                "$set": {"last_updated": datetime.utcnow()},
            },
        )

    async def get_current_cycle_number(self) -> int:
        doc = await self.get_state()
        if doc is None:
            return 0
        return doc.get("cycle_number", 0)

    async def count_remaining(self) -> int:
        doc = await self.get_state()
        if doc is None:
            return 0
        return len(doc.get("remaining_ids", []))


# ── Singletons ────────────────────────────────────────────────────────────────
queue_repo = QueueRepository()
state_repo = StateRepository()
sent_log_repo = SentLogRepository()
vault_replay_repo = VaultReplayRepository()
