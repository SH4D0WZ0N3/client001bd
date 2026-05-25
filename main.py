"""
Entry point — startup lifecycle, peer warm-up, graceful shutdown.

ROOT CAUSE FIX (PeerIdInvalid for target channel):

  Pyrogram (MTProto mode) maintains a local SQLite peer cache that maps
  channel IDs to their access_hashes.  When copy_message / copy_media_group
  is called with a channel ID that isn't in this cache, Pyrogram raises
  PeerIdInvalid immediately — without making any network request.

  The previous implementation called get_chat(id) in a retry loop.
  get_chat() also requires the peer to already be in the local cache,
  so it fails with the same error and never makes progress.

  The CORRECT fix is get_dialogs(), which calls the messages.getDialogs
  MTProto method.  This returns every dialog the bot has access to —
  including channels it is admin of — along with full peer objects
  (channel_id + access_hash).  Pyrogram stores these in its local cache
  automatically.  After get_dialogs() completes, all channel peers are
  resolvable regardless of whether any update was ever received from them.

  The source channel resolved in the old code only because a message was
  sent there, triggering an update that contained the peer object.  The
  target channel will NEVER self-resolve via an update because the bot
  only posts TO it — it receives no incoming updates FROM it.
"""

import asyncio
import signal
from loguru import logger

from app.bot import create_bot_instance
from app.database.database import (
    close_mongo_connection,
    connect_to_mongo,
    ensure_indexes,
)
from app.database.repositories import queue_repo
from app.scheduler.scheduler import setup_scheduler
from app.services.bootstrap import initial_channel_scan
from app.services.queue_manager import queue_manager
from app.services.telegram_sender import TelegramSender
from app.utils.config import settings
from app.utils.logging import setup_logging


def _async_exception_handler(loop, context):
    msg = context.get("exception", context.get("message", "unknown"))
    logger.critical(
        f"Unhandled async exception: {msg}",
        exc_info=context.get("exception"),
    )


async def _warm_up_peers(app) -> bool:
    """
    Pre-populate Pyrogram's local peer cache for both channels.

    Strategy (in order):

      1. get_dialogs() — fetches every dialog the bot has access to,
         including channels it is admin of, storing their access_hashes
         in Pyrogram's local cache.  This is reliable and fast (single
         paginated MTProto call).  Does NOT require any incoming update.

      2. Fallback retry loop — polls get_chat() every 10 s for up to
         100 s.  Only useful for the source channel if an incoming update
         arrives during this window; will never resolve the target channel
         on its own.

    Returns True when both peers are cached, False otherwise.
    """
    logger.info("Warming up channel peers via get_dialogs()…")

    found = {
        settings.SOURCE_CHANNEL_ID: False,
        settings.TARGET_CHAT_ID: False,
    }

    # ── Primary: get_dialogs() ────────────────────────────────────────────────
    try:
        async for dialog in app.get_dialogs():
            cid = dialog.chat.id
            if cid in found and not found[cid]:
                found[cid] = True
                label = (
                    "SOURCE" if cid == settings.SOURCE_CHANNEL_ID else "TARGET"
                )
                title = getattr(dialog.chat, "title", str(cid))
                logger.info(
                    f"{label} peer cached via get_dialogs(): "
                    f"'{title}' (id={cid})"
                )
            if all(found.values()):
                break  # Both found — no need to continue paginating

    except Exception as exc:
        logger.warning(
            f"get_dialogs() failed: {exc}. "
            "Falling back to retry loop (source-only resolution)."
        )

    if all(found.values()):
        logger.success("Both channel peers resolved via get_dialogs().")
        return True

    # Report which peers are still missing after get_dialogs()
    for cid, resolved in found.items():
        if not resolved:
            label = "SOURCE" if cid == settings.SOURCE_CHANNEL_ID else "TARGET"
            logger.warning(
                f"{label} channel (id={cid}) not found in get_dialogs() results. "
                "Possible causes: (1) bot is NOT admin in this channel, "
                "(2) channel ID is wrong. Trying retry loop…"
            )

    # ── Fallback: retry loop ──────────────────────────────────────────────────
    # Useful ONLY for the source channel, which will auto-cache once any
    # incoming update arrives.  The target channel will NOT resolve here.
    for attempt in range(1, 11):
        await asyncio.sleep(10)

        for cid in list(found.keys()):
            if found[cid]:
                continue
            try:
                chat = await app.get_chat(cid)
                found[cid] = True
                label = "SOURCE" if cid == settings.SOURCE_CHANNEL_ID else "TARGET"
                logger.info(
                    f"{label} peer resolved on retry attempt {attempt}: "
                    f"'{chat.title}' (id={chat.id})"
                )
            except Exception as exc:
                label = "SOURCE" if cid == settings.SOURCE_CHANNEL_ID else "TARGET"
                logger.warning(
                    f"{label} peer not yet cached "
                    f"(attempt {attempt}/10): {exc}"
                )

        if all(found.values()):
            logger.success("Both channel peers resolved.")
            return True

    # ── Warmup failed ─────────────────────────────────────────────────────────
    for cid, resolved in found.items():
        if resolved:
            continue
        label = "SOURCE" if cid == settings.SOURCE_CHANNEL_ID else "TARGET"
        logger.error(
            f"Peer warmup failed for {label} channel (id={cid}).\n"
            f"  The bot cannot send to this channel until the peer is cached.\n"
            f"  REQUIRED ACTION — choose one:\n"
            f"    (a) Remove the bot from this channel, then re-add it as admin.\n"
            f"        The 'bot added' event will cache the peer automatically.\n"
            f"    (b) If this is the SOURCE channel, send any message there;\n"
            f"        the incoming update will cache the peer.\n"
            f"  Items will be re-queued as 'pending' and will send automatically\n"
            f"  once the peer is resolved — no data will be lost."
        )

    return all(found.values())


async def main() -> None:
    setup_logging()

    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_async_exception_handler)

    # ── Database ──────────────────────────────────────────────────────────────
    await connect_to_mongo()
    await ensure_indexes()
    await queue_repo.recover_stale_processing_items()
    await queue_repo.recover_send_failed_items()

    # ── Bot client ────────────────────────────────────────────────────────────
    app = create_bot_instance()
    sender = TelegramSender(app)

    await app.start()
    logger.success("Bot client started.")

    # ── Peer warm-up ──────────────────────────────────────────────────────────
    # Must run BEFORE the scheduler starts so the first posting tick can
    # actually send.  Uses get_dialogs() as the primary resolution method —
    # this correctly resolves the target channel even though the bot never
    # receives incoming updates from it.
    peers_ready = await _warm_up_peers(app)

    if peers_ready:
        logger.success("Peer warm-up complete. Scheduler will fire immediately.")
    else:
        logger.warning(
            "Peer warm-up incomplete. Scheduler will start regardless — "
            "failed sends are re-queued as pending and will retry automatically "
            "once the peer(s) are resolved via the action described above."
        )

    # ── Bootstrap (historical scan) ───────────────────────────────────────────
    try:
        await initial_channel_scan(app)
    except Exception as exc:
        logger.error(
            f"Initial channel scan failed (bot continues): {exc}",
            exc_info=True,
        )

    # ── Scheduler ─────────────────────────────────────────────────────────────
    # Always starts immediately regardless of peer warm-up status.
    # If a peer is still missing, sends fail with PeerIdInvalid, items are
    # re-queued as pending, and will succeed automatically once the peer
    # resolves (either via the actions above or on the next restart with
    # get_dialogs() succeeding).
    scheduler = setup_scheduler(sender, peers_ready=peers_ready)

    # ── Signal handling ───────────────────────────────────────────────────────
    stop_event = asyncio.Event()

    def _handle_signal(sig):
        logger.warning(f"Signal {sig.name} received. Initiating shutdown…")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal, sig)

    logger.info("Bot is running. Waiting for stop signal…")

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        logger.warning("Main task cancelled.")
    finally:
        logger.info("Shutting down scheduler…")
        if scheduler.running:
            scheduler.shutdown(wait=False)

        logger.info("Draining pending album flush tasks…")
        await queue_manager.shutdown()

        logger.info("Stopping bot client…")
        await app.stop()

        logger.info("Closing database connection…")
        await close_mongo_connection()

        logger.success("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except BaseException as exc:
        logger.opt(exception=True).critical("Fatal error: {}", str(exc).replace("{", "{{").replace("}", "}}"))
