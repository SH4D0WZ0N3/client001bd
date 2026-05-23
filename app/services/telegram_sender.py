# app/services/telegram_sender.py
import asyncio
from pyrogram import Client, enums
from pyrogram.errors import FloodWait, MessageIdInvalid, ChannelInvalid
from pyrogram.types import Message
from typing import List, Optional
from loguru import logger
from app.utils.config import settings
from app.database.models import QueueItem, SentLog
from app.database.repositories import sent_log_repo

class TelegramSender:
    def __init__(self, client: Client):
        self.client = client

    def _build_caption(self, original_caption: Optional[str]) -> str:
        parts = []
        if original_caption:
            parts.append(original_caption)
        if settings.FIXED_CAPTION:
            if parts:
                parts.append("\n" + "—" * 10 + "\n")
            parts.append(settings.FIXED_CAPTION)
        return "".join(parts)

    async def send_item(self, item: QueueItem) -> bool:
        try:
            if item.media_group_id and item.message_ids:
                logger.info(f"Sending media group {item.media_group_id} ({len(item.message_ids)} items)...")
                sent_messages = await self._send_media_group(item)
            else:
                logger.info(f"Sending single message {item.message_id}...")
                sent_messages = await self._send_single_message(item)

            if sent_messages:
                log = SentLog(
                    source_message_id=item.message_id,
                    target_chat_id=settings.TARGET_CHAT_ID,
                    target_message_ids=[m.id for m in sent_messages],
                    status="success",
                )
                await sent_log_repo.create_log(log)
                logger.success(f"Sent item from source message {item.message_id}.")
                return True
            return False

        except FloodWait:
            raise  # Let the worker handle FloodWait
        except (MessageIdInvalid, ChannelInvalid) as e:
            logger.warning(f"Message {item.message_id} unavailable: {e}. Marking failed.")
            return False
        except Exception as e:
            logger.error(f"Failed to send item {item.message_id}: {e}", exc_info=True)
            return False

    async def _send_single_message(self, item: QueueItem) -> List[Message]:
        original = await self.client.get_messages(
            settings.SOURCE_CHANNEL_ID, item.message_id
        )

        if not original or original.empty:
            raise MessageIdInvalid(f"Message {item.message_id} is empty or deleted.")

        caption = self._build_caption(
            original.caption.html if original.caption else None
        )

        sent = await self.client.copy_message(
            chat_id=settings.TARGET_CHAT_ID,
            from_chat_id=settings.SOURCE_CHANNEL_ID,
            message_id=item.message_id,
            caption=caption,
            parse_mode=enums.ParseMode.HTML,
        )
        return [sent]

    async def _send_media_group(self, item: QueueItem) -> List[Message]:
        messages = await self.client.get_messages(
            settings.SOURCE_CHANNEL_ID, item.message_ids
        )

        # Filter out empty/deleted messages and sort by ID
        valid = sorted(
            [m for m in messages if m and not m.empty],
            key=lambda m: m.id
        )

        if not valid:
            raise MessageIdInvalid(
                f"All messages in group {item.media_group_id} are deleted."
            )

        original_caption = next(
            (m.caption.html for m in valid if m.caption), None
        )
        caption = self._build_caption(original_caption)

        # captions must be a list: first item gets the full caption, rest empty
        captions = [caption] + [""] * (len(valid) - 1)

        sent_messages = await self.client.copy_media_group(
            chat_id=settings.TARGET_CHAT_ID,
            from_chat_id=settings.SOURCE_CHANNEL_ID,
            message_id=valid[0].id,
            captions=captions,
        )
        return sent_messages