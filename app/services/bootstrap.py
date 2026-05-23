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
    Pyrogram 2.x raises ValueError("Peer id invalid: ...") — a plain Python
    ValueError — when a peer's access_hash is not yet in the local session
    cache. This is different from pyrogram.errors.PeerIdInvalid which is a
    Telegram RPC error (400) that only arrives if the request reaches Telegram.

    For a bot client on a fresh session, the access_hash for a channel is only
    cached after the bot receives its first update from that channel. Until
    then, every get_chat() / get_chat_history() call raises ValueError.

    Strategy: retry with delays. Once the bot receives any update from the
    source channel (e.g. an admin posts something), the peer resolves and the
    scan proceeds. On subsequent restarts the session file already has the
    cache populated so this resolves on attempt 1.
    """
    last_exc: Exception | None = None

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

        except (PeerIdInvalid, ChannelInvalid, ValueError) as exc:
            # ValueError("Peer id invalid: ...") = not yet in local cache
            # PeerIdInvalid / ChannelInvalid  = Telegram RPC error
            # Both mean: retry after a delay
            last_exc = exc
            logger.warning(
                f"Source channel peer not yet resolved "
                f"(attempt {attempt}/{_PEER_RESOLVE_RETRIES}). "
                f"Waiting for first update from source channel. "
                f"Retrying in {_PEER_RESOLVE_DELAY}s… [{exc}]"
            )
            await asyncio.sleep(_PEER_RESOLVE_DELAY)

        except Exception as exc:
            # Any other error is genuinely unexpected — fail fast
            raise RuntimeError(
                f"Unexpected error resolving source channel "
                f"{settings.SOURCE_CHANNEL_ID}: {exc}"
            ) from exc

    raise RuntimeError(
        f"Cannot resolve source channel {settings.SOURCE_CHANNEL_ID} "
        f"after {_PEER_RESOLVE_RETRIES} attempts. "
        f"Last error: {last_exc}. "
        f"Verify: (1) SOURCE_CHANNEL_ID is correct, "
        f"(2) bot is an admin in that channel, "
        f"(3) at least one message has been sent in the channel "
        f"so the bot session can cache the peer."
    )


async def initial_channel_scan(client: Client) -> None:
    """
    Scans the source channel forward from START_MESSAGE_ID and inserts every
    non-service message into the queue.

    Skipped entirely when state already records a processed message ID that
    is strictly greater than START_MESSAGE_ID.
    """
    state = await state_repo.get_state()
    if state is not None and state.last_processed_message_id > settings.START_MESSAGE_ID:
        logger.info(
            f"Initial scan already completed "
            f"(last_processed_message_id={state.last_processed_message_id}). Skipping."
        )
        return

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