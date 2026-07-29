# Module purpose

Minimal Discord notification client — one function, no bot/gateway
connection. Just POSTs a message to a webhook URL.

# Module files

- `client.py` — `send_webhook(content, embeds=None)`. Reads
  `DISCORD_WEBHOOK_URL` from the environment at call time (fails loudly if
  unset). Deliberately omits the `embeds` key entirely when none are given
  (rather than sending `[]`) — a message carrying a bare URL *alongside* any
  embeds array unreliably fails to also get Discord's own auto-unfurl for
  that URL (confirmed by testing). See `web/routers/workflow/todo.py`'s
  `_notify_discord_for_review`, which relies on this by sending the asset
  preview link in its own separate call with no embeds attached.

# Who calls this

`web/routers/workflow/todo.py` (asset-ready-for-review notifications, login/logout
events indirectly via job logging). Raises on any non-2xx response or
network error — callers that need a failed notification to not break their
own request must catch it themselves.
