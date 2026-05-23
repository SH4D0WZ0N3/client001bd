import asyncio
from typing import List, Optional
from pyrogram import Client, enums
from pyrogram.errors import FloodWait, MessageIdInvalid, ChannelInvalid, PeerIdInvalid
from pyrogram.types import Message
from loguru import logger
from app.utils.config import settings
from app.database.models import QueueItem, SentLog
from app.database.repositories import sent_log_repo

# Telegram caption hard limit
_CAPTION_MAX_LEN = 1024


class TelegramSender:
    def __init__(self, client: Client) -> None:
        self.client = client

    # ------------------------------------------------------------------
    # Caption helpers
    # ------------------------------------------------------------------

    def _build_caption(self, original_html: Optional[str]) -> str:
        parts: List[str] = []

        if original_html:
            parts.append(original_html)

        if settings.FIXED_CAPTION:
            if parts:
                parts.append("\n" + "—" * 10 + "\n")
            parts.append(settings.FIXED_CAPTION)

        caption = "".join(parts)

        if len(caption) > _CAPTION_MAX_LEN:
            logger.warning(
                f"Caption length {len(caption)} exceeds Telegram limit "
                f"{_CAPTION_MAX_LEN}. Truncating."
            )
            caption = caption[:_CAPTION_MAX_LEN]

        return caption

    # ------------------------------------------------------------------
    # Public send entry-point
    # ------------------------------------------------------------------

    async def send_item(self, item: QueueItem) -> bool:
        """
        Returns True on success, False on permanent failure (deleted message,
        invalid peer, etc.). Raises FloodWait to let the worker handle backoff.
        """
        try:
            if item.media_group_id and item.message_ids:
                logger.info(
                    f"Sending media group {item.media_group_id} "
                    f"({len(item.message_ids)} items)…"
                )
                sent = await self._send_media_group(item)
            else:
                logger.info(f"Sending single message {item.message_id}…")
                sent = await self._send_single(item)

            if sent:
                await sent_log_repo.create_log(
                    SentLog(
                        source_message_id=item.message_id,
                        target_chat_id=settings.TARGET_CHAT_ID,
                        target_message_ids=[m.id for m in sent],
                        status="success",
                    )
                )
                logger.success(f"Sent source message {item.message_id}.")
                return True

            logger.warning(f"send_item: no messages returned for {item.message_id}.")
            return False

        except FloodWait:
            # Propagate to worker — it will re-queue and respect the interval.
            raise

        except (MessageIdInvalid, ChannelInvalid, PeerIdInvalid) as exc:
            logger.warning(
                f"Permanent Telegram error for message {item.message_id}: {exc}. "
                "Marking as failed."
            )
            return False

        except Exception as exc:
            logger.error(
                f"Unexpected error sending message {item.message_id}: {exc}",
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------
    # Single message
    # ------------------------------------------------------------------

    async def _send_single(self, item: QueueItem) -> List[Message]:
        original = await self.client.get_messages(
            settings.SOURCE_CHANNEL_ID, item.message_id
        )

        if original is None or original.empty:
            raise MessageIdInvalid(
                f"Message {item.message_id} is deleted or unavailable."
            )

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

    # ------------------------------------------------------------------
    # Media group
    # ------------------------------------------------------------------

    async def _send_media_group(self, item: QueueItem) -> List[Message]:
        raw_messages = await self.client.get_messages(
            settings.SOURCE_CHANNEL_ID, item.message_ids
        )

        # Filter out deleted / empty messages and sort by ascending ID
        valid: List[Message] = sorted(
            [m for m in raw_messages if m and not m.empty],
            key=lambda m: m.id,
        )

        if not valid:
            raise MessageIdInvalid(
                f"All messages in media group {item.media_group_id} are deleted."
            )

        if len(valid) < len(item.message_ids):
            logger.warning(
                f"Media group {item.media_group_id}: expected {len(item.message_ids)} "
                f"messages, got {len(valid)} (rest deleted). Sending partial album."
            )

        # Extract the first non-empty caption from any message in the group
        original_html: Optional[str] = next(
            (m.caption.html for m in valid if m.caption), None
        )
        caption = self._build_caption(original_html)

        # Pyrogram copy_media_group accepts captions as List[str]:
        # index 0 → first media item caption, rest → empty string
        captions: List[str] = [caption] + [""] * (len(valid) - 1)

        sent = await self.client.copy_media_group(
            chat_id=settings.TARGET_CHAT_ID,
            from_chat_id=settings.SOURCE_CHANNEL_ID,
            message_id=valid[0].id,
            captions=captions,
        )
        return sent
