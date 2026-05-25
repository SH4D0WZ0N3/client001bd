from __future__ import annotations

from datetime import datetime, date
from typing import Optional

import motor.motor_asyncio
from bson import ObjectId
from loguru import logger

from app.config import settings

# ── Module-level singletons ───────────────────────────────────────────────────
_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
_db: Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None


def get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not connected. Call connect_to_mongo() first.")
    return _db


# ── Lifecycle ─────────────────────────────────────────────────────────────────
async def connect_to_mongo() -> None:
    global _client, _db
    logger.info("Connecting to MongoDB...")
    _client = motor.motor_asyncio.AsyncIOMotorClient(
        settings.MONGO_URI,
        serverSelectionTimeoutMS=10_000,
    )
    # Validate connection
    await _client.admin.command("ping")
    _db = _client[settings.MONGO_DB]
    logger.info(f"MongoDB connected. Database: '{settings.MONGO_DB}'")


async def close_mongo_connection() -> None:
    global _client, _db
    if _client:
        logger.info("Closing MongoDB connection.")
        _client.close()
        _client = None
        _db = None


# ── Index setup ───────────────────────────────────────────────────────────────
async def ensure_indexes() -> None:
    db = get_db()
    logger.info("Ensuring MongoDB indexes...")

    await db.queue.create_index("status")
    await db.queue.create_index("created_at")
    await db.queue.create_index("source_message_id", unique=True)
    await db.queue.create_index([("status", 1), ("created_at", 1)])

    # Scan state: only one document ever exists
    await db.scan_state.create_index("key", unique=True)

    # Daily stats: (date, key) is unique
    await db.daily_stats.create_index([("date", 1), ("key", 1)], unique=True)

    logger.info("MongoDB indexes ensured.")


# ── Queue helpers ─────────────────────────────────────────────────────────────
async def insert_queue_item(item_dict: dict) -> str:
    """Insert a queue item; silently ignore duplicate source_message_id."""
    db = get_db()
    try:
        result = await db.queue.insert_one(item_dict)
        return str(result.inserted_id)
    except Exception as exc:
        if "duplicate key" in str(exc).lower() or "E11000" in str(exc):
            return None   # already queued — not an error
        raise


async def dequeue_next_item() -> Optional[dict]:
    """
    Atomically pull the oldest pending item and mark it 'processing'.
    Returns None if queue is empty.
    """
    db = get_db()
    doc = await db.queue.find_one_and_update(
        {"status": "pending"},
        {
            "$set": {
                "status": "processing",
                "updated_at": datetime.utcnow(),
            }
        },
        sort=[("created_at", 1)],
        return_document=True,
    )
    return doc


async def mark_item_done(item_id: str) -> None:
    db = get_db()
    await db.queue.update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"status": "done", "updated_at": datetime.utcnow()}},
    )


async def requeue_item(item_id: str) -> None:
    """Return an item to 'pending' so it retries next tick."""
    db = get_db()
    await db.queue.update_one(
        {"_id": ObjectId(item_id)},
        {
            "$set": {"status": "pending", "updated_at": datetime.utcnow()},
            "$inc": {"retry_count": 1},
        },
    )


async def recover_stale_processing() -> int:
    """
    On startup, any item left in 'processing' state (from a crashed run)
    is returned to 'pending'.
    """
    db = get_db()
    result = await db.queue.update_many(
        {"status": "processing"},
        {"$set": {"status": "pending", "updated_at": datetime.utcnow()}},
    )
    return result.modified_count


# ── Scan state ────────────────────────────────────────────────────────────────
async def get_last_processed_message_id() -> Optional[int]:
    db = get_db()
    doc = await db.scan_state.find_one({"key": "last_processed_message_id"})
    return doc["value"] if doc else None


async def set_last_processed_message_id(msg_id: int) -> None:
    db = get_db()
    await db.scan_state.update_one(
        {"key": "last_processed_message_id"},
        {"$set": {"value": msg_id, "updated_at": datetime.utcnow()}},
        upsert=True,
    )


# ── Daily stats ───────────────────────────────────────────────────────────────
async def get_daily_sent_count(today: str) -> int:
    """today = 'YYYY-MM-DD' string."""
    db = get_db()
    doc = await db.daily_stats.find_one({"key": "sent_count", "date": today})
    return doc["count"] if doc else 0


async def increment_daily_sent_count(today: str) -> int:
    """Atomically increment and return the new count."""
    db = get_db()
    doc = await db.daily_stats.find_one_and_update(
        {"key": "sent_count", "date": today},
        {"$inc": {"count": 1}},
        upsert=True,
        return_document=True,
    )
    return doc["count"]