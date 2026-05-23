# app/services/telegram_sender.py
import asyncio
from pyrogram import Client
from pyrogram.errors import FloodWait
from pyrogram.types import Message
from typing import List, Union, Optional
from loguru import logger
from app.utils.config import settings
from app.database.models import QueueItem, SentLog
from app.database.repositories import sent_log_repo

class TelegramSender:
    def __init__(self, client: Client):
        self.client = client

    def _get_modified_caption(self, original_caption: Optional[str]) -> str:
        """Appends the fixed caption and watermark to the original caption."""
        caption_parts = []
        if original_caption:
            caption_parts.append(original_caption)
        
        # Add a separator if there was an original caption
        if caption_parts and settings.FIXED_CAPTION:
            caption_parts.append("\n" + "—" * 10 + "\n")

        if settings.FIXED_CAPTION:
            caption_parts.append(settings.FIXED_CAPTION)
        
        # The watermark is often part of the fixed caption, but can be added separately
        # if settings.WATERMARK and settings.WATERMARK not in settings.FIXED_CAPTION:
        #     caption_parts.append(f"\n{settings.WATERMARK}")

        return "".join(caption_parts)

    async def send_item(self, item: QueueItem) -> bool:
        """Sends a single message or a media group to the target channel."""
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
                    status="success"
                )
                await sent_log_repo.create_log(log)
                logger.success(f"Successfully sent item from source message {item.message_id}.")
                return True
            return False

        except FloodWait as e:
            logger.warning(f"FloodWait received. Sleeping for {e.value} seconds.")
            await asyncio.sleep(e.value)
            # Re-raise the exception to allow the worker to retry
            raise
        except Exception as e:
            logger.error(f"Failed to send item for source message {item.message_id}: {e}", exc_info=True)
            return False

    async def _send_single_message(self, item: QueueItem) -> List[Message]:
        """Copies a single message."""
        original_message = await self.client.get_messages(settings.SOURCE_CHANNEL_ID, item.message_id)
        
        if not original_message:
            raise ValueError(f"Message {item.message_id} not found in source channel.")

        caption = self._get_modified_caption(original_message.caption.html if original_message.caption else None)
        
        sent_message = await self.client.copy_message(
            chat_id=settings.TARGET_CHAT_ID,
            from_chat_id=settings.SOURCE_CHANNEL_ID,
            message_id=item.message_id,
            caption=caption
        )
        return [sent_message]

    async def _send_media_group(self, item: QueueItem) -> List[Message]:
        """Copies a media group, applying the caption to the first item."""
        messages = await self.client.get_messages(settings.SOURCE_CHANNEL_ID, item.message_ids)
        
        # Find the message with the original caption in the group
        original_caption = None
        for msg in messages:
            if msg.caption:
                original_caption = msg.caption.html
                break
        
        caption = self._get_modified_caption(original_caption)

        sent_messages = await self.client.copy_media_group(
            chat_id=settings.TARGET_CHAT_ID,
            from_chat_id=settings.SOURCE_CHANNEL_ID,
            message_id=messages[0].id, # The first message ID triggers the whole group
            captions=caption
        )
        return sent_messages