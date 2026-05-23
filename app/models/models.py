"""
Database models.

Pydantic v2-compatible ObjectId handling: uses BeforeValidator + Annotated
instead of the v1-only __get_validators__ classmethod.
"""
from __future__ import annotations

from typing import Annotated, Any, List, Optional
from datetime import datetime

from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, Field


# ---------------------------------------------------------------------------
# Pydantic v2-compatible ObjectId validator
# ---------------------------------------------------------------------------

def _validate_object_id(v: Any) -> ObjectId:
    if isinstance(v, ObjectId):
        return v
    if ObjectId.is_valid(v):
        return ObjectId(str(v))
    raise ValueError(f"Invalid ObjectId value: {v!r}")


# Use Annotated + BeforeValidator — this is the correct Pydantic v2 pattern.
# It is also Pydantic v1-compatible via the compatibility shim if needed.
PyObjectId = Annotated[ObjectId, BeforeValidator(_validate_object_id)]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class QueueItem(BaseModel):
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    message_id: int
    media_group_id: Optional[str] = None
    message_ids: Optional[List[int]] = None
    status: str = "pending"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    scheduled_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    retry_count: int = 0
    error_message: Optional[str] = None

    model_config = {
        "arbitrary_types_allowed": True,
        "populate_by_name": True,   # allow both alias and field name
        "json_encoders": {ObjectId: str},
    }


class State(BaseModel):
    id: str = Field(alias="_id")
    last_processed_message_id: int = 0
    daily_sent_count: int = 0
    last_reset_date: str = ""

    model_config = {
        "arbitrary_types_allowed": True,
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }


class SentLog(BaseModel):
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    source_message_id: int
    target_chat_id: int
    target_message_ids: List[int]
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    status: str

    model_config = {
        "arbitrary_types_allowed": True,
        "populate_by_name": True,
        "json_encoders": {ObjectId: str},
    }
