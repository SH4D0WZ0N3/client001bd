# app/database/models.py
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from bson import ObjectId

class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v, *args, **kwargs):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, field_schema):
        field_schema.update(type="string")


class QueueItem(BaseModel):
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    message_id: int
    media_group_id: Optional[str] = None
    message_ids: Optional[List[int]] = None # For media groups
    status: str = "pending"  # pending, processing, sent, failed
    created_at: datetime = Field(default_factory=datetime.utcnow)
    scheduled_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    retry_count: int = 0
    error_message: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class State(BaseModel):
    id: str = Field(alias="_id")
    last_processed_message_id: int
    daily_sent_count: int
    last_reset_date: str # YYYY-MM-DD

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class SentLog(BaseModel):
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    source_message_id: int
    target_chat_id: int
    target_message_ids: List[int]
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    status: str # success, failure

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}