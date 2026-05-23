# app/database/database.py
from motor.motor_asyncio import AsyncIOMotorClient
from loguru import logger
from app.utils.config import settings

class Database:
    client: AsyncIOMotorClient = None

db = Database()

async def connect_to_mongo():
    logger.info("Connecting to MongoDB...")
    db.client = AsyncIOMotorClient(settings.MONGO_URI)
    try:
        await db.client.admin.command('ping')
        logger.info("MongoDB connection successful.")
    except Exception as e:
        logger.error(f"Could not connect to MongoDB: {e}")
        raise

async def close_mongo_connection():
    if db.client:
        logger.info("Closing MongoDB connection.")
        db.client.close()

def get_database():
    if db.client is None:
        raise Exception("Database not initialized. Call connect_to_mongo first.")
    return db.client.get_default_database()