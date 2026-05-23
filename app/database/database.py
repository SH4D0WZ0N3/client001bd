from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import uri_parser, ASCENDING
from loguru import logger
from app.utils.config import settings


class _Database:
    client: AsyncIOMotorClient = None
    name: str = None


_db = _Database()


def _extract_db_name(uri: str) -> str:
    try:
        parsed = uri_parser.parse_uri(uri)
        name = parsed.get("database")
        if name:
            return name
    except Exception as exc:
        logger.warning(f"Could not parse database name from URI: {exc}")
    return "telegram_bot"


async def connect_to_mongo() -> None:
    logger.info("Connecting to MongoDB...")
    _db.name = _extract_db_name(settings.MONGO_URI)
    _db.client = AsyncIOMotorClient(settings.MONGO_URI)
    try:
        await _db.client.admin.command("ping")
        logger.info(f"MongoDB connected. Database: '{_db.name}'")
    except Exception as exc:
        logger.error(f"MongoDB connection failed: {exc}")
        raise


async def close_mongo_connection() -> None:
    if _db.client is not None:
        logger.info("Closing MongoDB connection.")
        _db.client.close()


def get_database() -> AsyncIOMotorDatabase:
    if _db.client is None:
        raise RuntimeError("Database not initialized. Call connect_to_mongo() first.")
    return _db.client[_db.name]


async def ensure_indexes() -> None:
    database = get_database()
    logger.info("Ensuring MongoDB indexes...")

    # queue — compound index for ordered pending item retrieval
    await database["queue"].create_index(
        [("status", ASCENDING), ("message_id", ASCENDING)],
        name="idx_status_message_id",
        background=True,
    )
    # queue — unique constraint on message_id prevents duplicate inserts atomically
    await database["queue"].create_index(
        [("message_id", ASCENDING)],
        unique=True,
        name="idx_message_id_unique",
        background=True,
    )
    # queue — media group lookups
    await database["queue"].create_index(
        [("media_group_id", ASCENDING)],
        name="idx_media_group_id",
        sparse=True,
        background=True,
    )

    # sent_logs
    await database["sent_logs"].create_index(
        [("source_message_id", ASCENDING)],
        name="idx_source_message_id",
        background=True,
    )
    await database["sent_logs"].create_index(
        [("sent_at", ASCENDING)],
        name="idx_sent_at",
        background=True,
    )

    logger.info("MongoDB indexes ensured.")
