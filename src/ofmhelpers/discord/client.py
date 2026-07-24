"""
ofmhelpers/discord/client.py

Minimal Discord notification client: POST a message to a webhook URL.
Nothing else (no bot, no gateway connection) -- that's all this app needs.
See https://discord.com/developers/docs/resources/webhook#execute-webhook.
"""

import os

import requests


def send_webhook(content: str, embeds: list[dict] | None = None) -> None:
    """Required env var, read at call time (like gdrive's
    GOOGLE_DRIVE_FOLDER_ID) -- fail loudly if it's unset rather than
    silently dropping the notification. Raises on any non-2xx response or
    network error; callers that need this to not break their own request
    should catch it themselves."""
    url = os.environ["DISCORD_WEBHOOK_URL"]
    resp = requests.post(
        url, json={"content": content, "embeds": embeds or []}, timeout=10
    )
    resp.raise_for_status()
