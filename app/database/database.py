# app/database/database.py
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import uri_parser, ASCENDING
from pymongo.errors import DuplicateKeyError
from loguru import logger
from app.utils.config import settings

class Database:
    client: AsyncIOMotorClient = None
    name: str = None

db = Database()

def _extract_db_name(uri: str) -> str:
    try:
        parsed = uri_parser.parse_uri(uri)
        return parsed.get("database") or "telegram_bot"
    except Exception:
        return "telegram_bot"

async def connect_to_mongo():
    logger.info("Connecting to MongoDB...")
    db.name = _extract_db_name(settings.MONGO_URI)
    db.client = AsyncIOMotorClient(settings.MONGO_URI)
    try:
        await db.client.admin.command('ping')
        logger.info(f"MongoDB connection successful. Database: '{db.name}'")
    except Exception as e:
        logger.error(f"Could not connect to MongoDB: {e}")
        raise

async def close_mongo_connection():
    if db.client:
        logger.info("Closing MongoDB connection.")
        db.client.close()

def get_database() -> AsyncIOMotorDatabase:
    if db.client is None:
        raise RuntimeError("Database not initialized. Call connect_to_mongo first.")
    return db.client[db.name]

async def ensure_indexes():
    """Create all required indexes. Safe to call multiple times (idempotent)."""
    database = get_database()
    logger.info("Ensuring database indexes...")

    # queue collection
    await database["queue"].create_index(
        [("status", ASCENDING), ("message_id", ASCENDING)],
        name="status_message_id"
    )
    await database["queue"].create_index(
        [("message_id", ASCENDING)],
        unique=True,
        name="message_id_unique"
    )
    await database["queue"].create_index(
        [("media_group_id", ASCENDING)],
        name="media_group_id",
        sparse=True
    )

    # sent_logs collection
    await database["sent_logs"].create_index(
        [("source_message_id", ASCENDING)],
        name="source_message_id"
    )
    await database["sent_logs"].create_index(
        [("sent_at", ASCENDING)],
        name="sent_at"
    )

    logger.info("Indexes ensured.")