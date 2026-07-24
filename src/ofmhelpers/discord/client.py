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
    should catch it themselves.

    Omits the "embeds" key entirely when none are given, rather than
    sending an empty list -- confirmed by testing that a webhook message
    carrying a bare URL in content *alongside* any embeds array (even one
    unrelated to that URL, e.g. just an approval button) unreliably fails
    to also get Discord's own auto-unfurl embed for that URL. A message
    with no embeds key at all -- the same shape a plain human-typed link
    produces -- unfurls reliably. See routers/todo.py's
    _notify_discord_for_review, which relies on this by sending the asset
    preview link in its own call with no embeds attached."""
    url = os.environ["DISCORD_WEBHOOK_URL"]
    payload = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
