"""
TelegramSender — copies source channel content to the target channel.

Fixes applied:
  HR-3: copy_media_group fallback now passes parse_mode=HTML so captions
        with HTML formatting are rendered correctly.
  HR-4: Watermarked media group uses InputMediaDocument for documents/audio
        instead of incorrectly wrapping them in InputMediaVideo.
"""

import asyncio
import io
import math
import os
import tempfile
from typing import List, Optional, Tuple

from pyrogram import Client, enums
from pyrogram.errors import FloodWait, MessageIdInvalid, ChannelInvalid, PeerIdInvalid
from pyrogram.types import Message
from loguru import logger

from app.utils.config import settings
from app.database.models import QueueItem, SentLog
from app.database.repositories import sent_log_repo

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False
    logger.warning("Pillow not installed. Watermarking disabled.")

_CAPTION_MAX_LEN = 1024

_WATERMARK_TEXT: str = settings.WATERMARK or ""
_WATERMARK_COUNT: int = int(getattr(settings, "WATERMARK_COUNT", 4))
_WATERMARK_OPACITY: int = int(getattr(settings, "WATERMARK_OPACITY", 30))
_WATERMARK_ROTATION: int = int(getattr(settings, "WATERMARK_ROTATION", -35))
_WATERMARK_FONT_SCALE: float = float(getattr(settings, "WATERMARK_FONT_SCALE", 0.04))

_WATERMARK_ENABLED: bool = _PIL_AVAILABLE and bool(_WATERMARK_TEXT)


# ---------------------------------------------------------------------------
# Watermark core  (CPU-bound — called via run_in_executor)
# ---------------------------------------------------------------------------

def _load_font(font_size: int):
    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for path in font_candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, font_size)
            except Exception:
                continue
    return ImageFont.load_default()


def _make_text_stamp(text, font, opacity: int, rotation: int) -> "Image.Image":
    scratch = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(scratch)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0] + 20
    th = bbox[3] - bbox[1] + 20
    txt_img = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt_img)
    d.text((10, 10), text, font=font, fill=(255, 255, 255, opacity))
    return txt_img.rotate(rotation, expand=True, resample=Image.BICUBIC)


def _apply_watermark_sync(image_bytes: bytes) -> bytes:
    """CPU-bound. Never call directly from async code — use run_in_executor."""
    if not _WATERMARK_ENABLED:
        return image_bytes
    try:
        img = Image.open(io.BytesIO(image_bytes))
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        img = img.convert("RGBA")
        w, h = img.size
        font_size = max(16, int(min(w, h) * _WATERMARK_FONT_SCALE))
        font = _load_font(font_size)
        stamp = _make_text_stamp(
            _WATERMARK_TEXT, font, _WATERMARK_OPACITY, _WATERMARK_ROTATION
        )
        sw, sh = stamp.size
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        count = max(1, _WATERMARK_COUNT)
        cols = math.ceil(math.sqrt(count))
        rows = math.ceil(count / cols)
        positions: List[Tuple[int, int]] = []
        for row in range(rows):
            for col in range(cols):
                if len(positions) >= count:
                    break
                x = int((col + 0.5) * w / cols) - sw // 2
                y = int((row + 0.5) * h / rows) - sh // 2
                positions.append((x, y))
        for x, y in positions:
            overlay.paste(stamp, (x, y), stamp)
        watermarked = Image.alpha_composite(img, overlay).convert("RGB")
        out = io.BytesIO()
        watermarked.save(out, format="JPEG", quality=92)
        return out.getvalue()
    except Exception as exc:
        logger.warning(f"Watermark processing failed, using original: {exc}")
        return image_bytes


async def _apply_watermark(image_bytes: bytes) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _apply_watermark_sync, image_bytes)


# ---------------------------------------------------------------------------
# Temp-file helpers
# ---------------------------------------------------------------------------

def _bytes_to_tempfile(data: bytes, suffix: str = ".jpg") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        os.close(fd)
        raise
    return path


def _cleanup(*paths: str) -> None:
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception as exc:
            logger.debug(f"Failed to remove temp file {p}: {exc}")


# ---------------------------------------------------------------------------
# Sender
# ---------------------------------------------------------------------------

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
                f"Caption length {len(caption)} exceeds limit "
                f"{_CAPTION_MAX_LEN}. Truncating."
            )
            caption = caption[:_CAPTION_MAX_LEN]
        return caption

    # ------------------------------------------------------------------
    # Watermark helper
    # ------------------------------------------------------------------

    async def _download_and_watermark(self, message: Message) -> Optional[str]:
        if not message.photo:
            return None
        if not _WATERMARK_ENABLED:
            return None
        try:
            raw = await self.client.download_media(message, in_memory=True)
            if raw is None:
                logger.warning(
                    f"download_media returned None for message {message.id}"
                )
                return None
            if isinstance(raw, io.BytesIO):
                raw_bytes = raw.getvalue()
            elif isinstance(raw, bytes):
                raw_bytes = raw
            else:
                logger.warning(
                    f"Unexpected download_media return type: {type(raw)} "
                    f"for message {message.id}"
                )
                return None
            processed = await _apply_watermark(raw_bytes)
            return _bytes_to_tempfile(processed, suffix=".jpg")
        except Exception as exc:
            logger.warning(
                f"download_and_watermark failed for message {message.id}: {exc}"
            )
            return None

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    async def send_item(self, item: QueueItem) -> bool:
        """
        Returns True on success, False on permanent failure.
        Raises FloodWait to let the worker handle backoff.
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

            logger.warning(
                f"send_item: no messages returned for {item.message_id}."
            )
            return False

        except FloodWait:
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

        if original.photo and _WATERMARK_ENABLED:
            temp_path = await self._download_and_watermark(original)
            if temp_path:
                try:
                    sent = await self.client.send_photo(
                        chat_id=settings.TARGET_CHAT_ID,
                        photo=temp_path,
                        caption=caption,
                        parse_mode=enums.ParseMode.HTML,
                    )
                    return [sent]
                except Exception as exc:
                    logger.warning(
                        f"send_photo with watermark failed for {item.message_id}: "
                        f"{exc}. Falling back to copy_message."
                    )
                finally:
                    _cleanup(temp_path)

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
        from pyrogram.types import (
            InputMediaPhoto,
            InputMediaVideo,
            InputMediaDocument,
            InputMediaAudio,
        )

        raw_messages = await self.client.get_messages(
            settings.SOURCE_CHANNEL_ID, item.message_ids
        )
        if not isinstance(raw_messages, list):
            raw_messages = [raw_messages]

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
                f"Media group {item.media_group_id}: expected "
                f"{len(item.message_ids)} messages, got {len(valid)}. "
                "Sending partial album."
            )

        original_html: Optional[str] = next(
            (m.caption.html for m in valid if m.caption), None
        )
        caption = self._build_caption(original_html)

        # --- Watermarked send path ---
        if _WATERMARK_ENABLED:
            temp_paths: List[Optional[str]] = []
            media_inputs = []
            first_caption_assigned = False

            try:
                for msg in valid:
                    item_caption = caption if not first_caption_assigned else ""

                    if msg.photo:
                        temp_path = await self._download_and_watermark(msg)
                        temp_paths.append(temp_path)
                        if temp_path:
                            media_inputs.append(
                                InputMediaPhoto(
                                    media=temp_path,
                                    caption=item_caption,
                                    parse_mode=enums.ParseMode.HTML,
                                )
                            )
                            first_caption_assigned = True
                        else:
                            raise RuntimeError(
                                f"Watermark failed for photo in msg {msg.id}"
                            )

                    elif msg.video:
                        temp_paths.append(None)
                        media_inputs.append(
                            InputMediaVideo(
                                media=msg.video.file_id,
                                caption=item_caption,
                                parse_mode=enums.ParseMode.HTML,
                            )
                        )
                        first_caption_assigned = True

                    elif msg.document:
                        # FIX HR-4: Use InputMediaDocument, not InputMediaVideo
                        temp_paths.append(None)
                        media_inputs.append(
                            InputMediaDocument(
                                media=msg.document.file_id,
                                caption=item_caption,
                                parse_mode=enums.ParseMode.HTML,
                            )
                        )
                        first_caption_assigned = True

                    elif msg.audio:
                        # FIX HR-4: Use InputMediaAudio, not InputMediaVideo
                        temp_paths.append(None)
                        media_inputs.append(
                            InputMediaAudio(
                                media=msg.audio.file_id,
                                caption=item_caption,
                                parse_mode=enums.ParseMode.HTML,
                            )
                        )
                        first_caption_assigned = True

                    else:
                        # video_note, sticker, or other unsupported media type
                        # in a media group — skip it rather than send a bad type
                        temp_paths.append(None)
                        logger.warning(
                            f"Message {msg.id} in group {item.media_group_id} "
                            f"has unsupported media type. Skipping this item."
                        )

                if media_inputs:
                    sent = await self.client.send_media_group(
                        chat_id=settings.TARGET_CHAT_ID,
                        media=media_inputs,
                    )
                    return sent

            except Exception as exc:
                logger.warning(
                    f"Watermarked media group send failed for "
                    f"{item.media_group_id}: {exc}. "
                    "Falling back to copy_media_group."
                )
            finally:
                _cleanup(*[p for p in temp_paths if p])

        # --- Fallback: copy_media_group (no watermark) ---
        # FIX HR-3: Add parse_mode so HTML in captions renders correctly.
        captions: List[str] = [caption] + [""] * (len(valid) - 1)
        sent = await self.client.copy_media_group(
            chat_id=settings.TARGET_CHAT_ID,
            from_chat_id=settings.SOURCE_CHANNEL_ID,
            message_id=valid[0].id,
            captions=captions,
            parse_mode=enums.ParseMode.HTML,
        )
        return sent
