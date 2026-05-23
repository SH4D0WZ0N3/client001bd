# app/database/repositories.py
from motor.motor_asyncio import AsyncIOMotorCollection
from bson import ObjectId
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
        Inserts a queue item. Uses the unique index on message_id for
        atomic duplicate prevention — no separate find_one needed.
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
        raw_item = await self.collection.find_one_and_update(
            {"status": "pending"},
            {"$set": {"status": "processing", "scheduled_at": datetime.utcnow()}},
            sort=[("message_id", 1)],
            return_document=True
        )
        if not raw_item:
            return None
        return QueueItem(**raw_item)

    async def update_item_status(
        self,
        item_id,
        status: str,
        error_message: Optional[str] = None
    ):
        object_id = ObjectId(item_id) if not isinstance(item_id, ObjectId) else item_id
        update_doc = {"$set": {"status": status}}
        if status == "sent":
            update_doc["$set"]["sent_at"] = datetime.utcnow()
        if status == "failed":
            update_doc["$set"]["error_message"] = error_message
            update_doc["$inc"] = {"retry_count": 1}
        if status == "pending":
            # Reset processing state on re-queue
            update_doc["$set"]["scheduled_at"] = None
            update_doc["$set"]["error_message"] = None

        result = await self.collection.update_one({"_id": object_id}, update_doc)
        if result.matched_count == 0:
            logger.warning(f"update_item_status: no document found for _id={object_id}")

class StateRepository(BaseRepository):
    def __init__(self):
        super().__init__("state")
        self.state_id = "main_state"

    async def get_state(self) -> Optional[State]:
        state_doc = await self.collection.find_one({"_id": self.state_id})
        return State(**state_doc) if state_doc else None

    async def update_state(self, last_processed_id: int):
        await self.collection.update_one(
            {"_id": self.state_id},
            {"$set": {"last_processed_message_id": last_processed_id}},
            upsert=True
        )

    async def reset_daily_counter(self):
        today = date.today().isoformat()
        await self.collection.update_one(
            {"_id": self.state_id},
            {"$set": {"daily_sent_count": 0, "last_reset_date": today}},
            upsert=True
        )

    async def increment_daily_sent_count(self):
        await self.collection.update_one(
            {"_id": self.state_id},
            {"$inc": {"daily_sent_count": 1}},
            upsert=True
        )

class SentLogRepository(BaseRepository):
    def __init__(self):
        super().__init__("sent_logs")

    async def create_log(self, log: SentLog):
        await self.collection.insert_one(log.model_dump(by_alias=True, exclude={"id"}))

queue_repo = QueueRepository()
state_repo = StateRepository()
sent_log_repo = SentLogRepository()