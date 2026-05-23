"""
Run this script ONCE on your local machine to generate a Pyrogram
StringSession for your user account.

    python generate_session.py

Copy the printed session string and add it to Railway as:
    USER_SESSION_STRING=<the string>

This script is never run in production.
"""
from pyrogram import Client
from pyrogram.types import TermsOfService

API_ID = int(input("Enter API_ID: ").strip())
API_HASH = input("Enter API_HASH: ").strip()

with Client(
    name="userbot_session_generator",
    api_id=API_ID,
    api_hash=API_HASH,
    in_memory=True,
) as app:
    session_string = app.export_session_string()
    print("\n" + "=" * 60)
    print("SESSION STRING (add to Railway as USER_SESSION_STRING):")
    print("=" * 60)
    print(session_string)
    print("=" * 60 + "\n")