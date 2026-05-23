from pyrogram import Client
from pyrogram.errors import FloodWait
from loguru import logger
from app.utils.config import settings
from app.database.repositories import state_repo
from app.services.queue_manager import queue_manager


async def _run_historical_scan_with_userbot() -> None:
    logger.info("Starting historical scan using userbot session...")

    userbot = Client(
        name="userbot_scanner",
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        session_string=settings.USER_SESSION_STRING,
        in_memory=True,
    )

    try:
        await userbot.start()
        logger.info("Userbot client started for historical scan.")

        try:
            chat = await userbot.get_chat(settings.SOURCE_CHANNEL_ID)
            logger.info(
                f"Source channel resolved via userbot: '{chat.title}' "
                f"(id={chat.id})"
            )
        except Exception as exc:
            raise RuntimeError(
                f"Userbot cannot resolve source channel "
                f"{settings.SOURCE_CHANNEL_ID}: {exc}. "
                f"Ensure the user account is a member of that channel."
            )

        offset = settings.START_MESSAGE_ID + 1
        logger.info(
            f"Scanning history. START_MESSAGE_ID={settings.START_MESSAGE_ID}, "
            f"offset_id={offset}"
        )

        collected: list = []
        skipped = 0

        async for message in userbot.get_chat_history(
            settings.SOURCE_CHANNEL_ID,
            offset_id=offset,
        ):
            if message.service:
                skipped += 1
                continue
            collected.append(message)

            if len(collected) % 200 == 0:
                logger.info(f"Collected {len(collected)} messages so far...")

        if not collected:
            logger.warning("Historical scan: no messages found.")
            return

        collected.sort(key=lambda m: m.id)

        logger.info(
            f"Collected {len(collected)} messages "
            f"(skipped {skipped} service messages). "
            f"ID range: {collected[0].id} – {collected[-1].id}. "
            f"Inserting into queue…"
        )

        queued = 0
        for message in collected:
            await queue_manager.add_message_to_queue(message)
            queued += 1

            if queued % 100 == 0:
                logger.info(
                    f"Queue progress: {queued}/{len(collected)}, "
                    f"current_id={message.id}"
                )

        last_id = collected[-1].id
        await state_repo.update_state(last_processed_id=last_id)

        logger.success(
            f"Historical scan complete. "
            f"queued={queued}, skipped={skipped}, last_id={last_id}"
        )

    finally:
        try:
            await userbot.stop()
            logger.info("Userbot client stopped.")
        except Exception:
            pass


async def initial_channel_scan(client: Client) -> None:
    state = await state_repo.get_state()

    if state is not None and state.last_processed_message_id > settings.START_MESSAGE_ID:
        logger.info(
            f"Historical scan already completed "
            f"(last_processed_message_id={state.last_processed_message_id}). "
            f"Skipping."
        )
        return

    if not settings.USER_SESSION_STRING:
        logger.warning(
            "USER_SESSION_STRING not set. Historical scan skipped. "
            "Only new messages arriving after bot startup will be queued."
        )
        if state is None:
            await state_repo.update_state(
                last_processed_id=settings.START_MESSAGE_ID
            )
        return

    try:
        await _run_historical_scan_with_userbot()
    except Exception as exc:
        logger.error(
            f"Historical scan failed: {exc}. "
            f"Bot continues — live messages will still be queued normally.",
            exc_info=True,
        )