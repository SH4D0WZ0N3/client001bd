from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from bson import ObjectId
from pydantic import BaseModel, Field, field_validator


class PyObjectId(str):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        if ObjectId.is_valid(v):
            return str(v)
        raise ValueError(f"Invalid ObjectId: {v}")


class QueueItem(BaseModel):
    """
    Represents one item in the posting queue.

    - Single message  → source_message_id set, message_ids = [source_message_id]
    - Album           → source_message_id = first msg id, message_ids = all ids,
                        media_group_id set
    """

    id: Optional[str] = Field(None, alias="_id")
    source_message_id: int
    message_ids: List[int] = Field(default_factory=list)
    media_group_id: Optional[str] = None   # always str; Pyrogram returns int — coerced below
    status: str = "pending"                # pending | processing | done | failed
    retry_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ── Fix: Pyrogram exposes media_group_id as int; force to str ────────────
    @field_validator("media_group_id", mode="before")
    @classmethod
    def coerce_media_group_id(cls, v):
        if v is None:
            return None
        return str(v)

    @field_validator("id", mode="before")
    @classmethod
    def coerce_object_id(cls, v):
        if v is None:
            return None
        return str(v)

    model_config = {"populate_by_name": True}

    def to_mongo(self) -> dict:
        """Return a dict suitable for MongoDB insertion (no _id key)."""
        d = self.model_dump(exclude={"id"})
        return d

    @classmethod
    def from_mongo(cls, doc: dict) -> "QueueItem":
        if doc is None:
            return None
        doc = dict(doc)
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return cls(**doc)
