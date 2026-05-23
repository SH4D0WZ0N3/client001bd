from pyrogram import Client, filters
from pyrogram.types import Message
from app.utils.config import settings


def register_command_handlers(app: Client) -> None:
    @app.on_message(filters.command("start") & filters.private)
    async def start_command(client: Client, message: Message) -> None:
        await message.reply_text(
            f"Hello! This bot automates content posting.\n\n"
            f"To see the public content, please join our channel:\n"
            f"{settings.PUBLIC_CHANNEL_LINK}"
        )
