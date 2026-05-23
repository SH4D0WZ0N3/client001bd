import asyncio
from pyrogram import Client
from pyrogram.errors import PeerIdInvalid, ChannelInvalid, FloodWait
from loguru import logger
from app.utils.config import settings
from app.database.repositories import state_repo
from app.services.queue_manager import queue_manager

_PEER_RESOLVE_RETRIES = 20
_PEER_RESOLVE_DELAY = 15  # seconds between retries


async def _resolve_peer_with_retry(client: Client) -> None:
    """
    Attempts to resolve the source channel peer repeatedly.
    For a bot client, the access_hash only becomes available after the bot
    receives at least one update from that channel OR after successful
    get_chat() — which itself requires a prior update on a fresh session.

    Strategy: keep retrying with delays. Once the bot is an admin and
    has received at least one message from the channel (or the session
    already has the peer cached from a previous run), this succeeds.
    """
    last_exc = None
    for attempt in range(1, _PEER_RESOLVE_RETRIES + 1):
        try:
            chat = await client.get_chat(settings.SOURCE_CHANNEL_ID)
            logger.info(
                f"Source channel resolved: '{chat.title}' "
                f"(id={chat.id}, type={chat.type})"
            )
            return
        except FloodWait as exc:
            logger.warning(f"FloodWait {exc.value}s during peer resolution.")
            await asyncio.sleep(exc.value)
        except (PeerIdInvalid, ChannelInvalid) as exc:
            last_exc = exc
            logger.warning(
                f"Source channel peer not yet resolved "
                f"(attempt {attempt}/{_PEER_RESOLVE_RETRIES}). "
                f"Ensure bot is admin in source channel. "
                f"Retrying in {_PEER_RESOLVE_DELAY}s…"
            )
            await asyncio.sleep(_PEER_RESOLVE_DELAY)
        except Exception as exc:
            raise RuntimeError(f"Unexpected error resolving source channel: {exc}")

    raise RuntimeError(
        f"Cannot resolve source channel {settings.SOURCE_CHANNEL_ID} "
        f"after {_PEER_RESOLVE_RETRIES} attempts. "
        f"Last error: {last_exc}. "
        f"Check SOURCE_CHANNEL_ID is correct and bot is an admin in that channel."
    )


async def initial_channel_scan(client: Client) -> None:
    """
    Scans the source channel forward from START_MESSAGE_ID and inserts every
    non-service message into the queue.

    Skipped entirely when state already records a processed message ID that
    is strictly greater than START_MESSAGE_ID.

    Peer resolution is retried up to _PEER_RESOLVE_RETRIES times with delays,
    so the scan can succeed even if the bot session is fresh and hasn't yet
    received any update from the source channel.
    """
    state = await state_repo.get_state()
    if state is not None and state.last_processed_message_id > settings.START_MESSAGE_ID:
        logger.info(
            f"Initial scan already completed "
            f"(last_processed_message_id={state.last_processed_message_id}). Skipping."
        )
        return

    # This blocks until peer is resolved or raises after all retries exhausted.
    await _resolve_peer_with_retry(client)

    offset = settings.START_MESSAGE_ID + 1

    logger.info(
        f"Starting initial channel scan. "
        f"START_MESSAGE_ID={settings.START_MESSAGE_ID}, offset_id={offset}"
    )

    collected: list = []
    skipped = 0

    try:
        async for message in client.get_chat_history(
            settings.SOURCE_CHANNEL_ID,
            offset_id=offset,
        ):
            if message.service:
                skipped += 1
                continue
            collected.append(message)

        if not collected:
            logger.warning("Initial scan found no messages to queue.")
            return

        collected.sort(key=lambda m: m.id)

        logger.info(
            f"Collected {len(collected)} messages (skipped {skipped} service). "
            f"ID range: {collected[0].id} – {collected[-1].id}. Queueing…"
        )

        queued = 0
        for message in collected:
            await queue_manager.add_message_to_queue(message)
            queued += 1

            if queued % 100 == 0:
                logger.info(
                    f"Scan progress: {queued}/{len(collected)} queued, "
                    f"current_id={message.id}"
                )

        last_id = collected[-1].id
        await state_repo.update_state(last_processed_id=last_id)

        logger.success(
            f"Initial scan complete. "
            f"queued={queued}, skipped={skipped}, last_id={last_id}"
        )

    except Exception as exc:
        logger.error(f"Initial channel scan failed: {exc}", exc_info=True)
        raise