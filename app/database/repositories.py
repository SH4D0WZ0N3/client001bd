from motor.motor_asyncio import AsyncIOMotorCollection
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from app.database.database import get_database
from app.database.models import QueueItem, State, SentLog
from typing import Optional
from datetime import datetime, date
from loguru import logger


class BaseRepository:
    def __init__(self, collection_name: str):
        self.collection: AsyncIOMotorCollection = get_database()[collection_name]


class QueueRepository(BaseRepository):
    def __init__(self):
        super().__init__("queue")

    async def add_to_queue(self, item: QueueItem) -> Optional[QueueItem]:
        """
        Inserts a new queue item. Relies on the unique index on message_id for
        atomic duplicate prevention — no separate find_one race condition.
        """
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

    async def update_item_status(
        self,
        item_id,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        object_id = item_id if isinstance(item_id, ObjectId) else ObjectId(str(item_id))

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

    async def reset_daily_counter(self) -> None:
        today = date.today().isoformat()
        await self.collection.update_one(
            {"_id": self.state_id},
            {
                "$set": {"daily_sent_count": 0, "last_reset_date": today},
                "$setOnInsert": {"last_processed_message_id": 0},
            },
            upsert=True,
        )

    async def increment_daily_sent_count(self) -> None:
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


queue_repo = QueueRepository()
state_repo = StateRepository()
sent_log_repo = SentLogRepository()
