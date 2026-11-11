# integrations/senders

Where events go out (Telegram, later Discord, email, ...). Any part of the app emits an `Event`; this package only delivers it.

```
emit(Event) | @emits(build)          features/events/bus.py     returns at once, never raises
  -> `events` queue                   compose worker (its own pool), 1 process, HIGHEST priority
  -> dispatch                         features/events/dispatch.py: the aggregator
       each sender in senders(), not in OFM_EVENTS_SENDERS_OFF, each of its targets()
  -> deliver(sender, target, event)   one RQ job each: 180 s limit, failed kept 14 d
       off since? key already sent to this target?  -> skipped
       wait Sender.pace_s(target)     Redis events:pace:*, never flooded
       Sender.send(target, event)     SendError(retry_after<=60) -> waited out, tried once more
                                      SendError(permanent)       -> gave_up, no retry
                                      SendError                  -> RQ retry [10, 60, 300], last = gave_up
       sent -> events:sent:<key>:<sender>:<target hash> (30 d)
```
- At least once: a send that timed out after the service took it may arrive twice. Order: as queued; a retried send comes later.
- Track: Logfire span `event {event} to {sender}`, attribute `outcome` = sent / skipped / retry / gave_up (never the target). Lost: `SELECT start_timestamp, attributes->>'event', attributes->>'sender', exception_message FROM records WHERE span_name = 'event {event} to {sender}' AND attributes->>'outcome' = 'gave_up'`. Failed-every-try jobs: RQ failed registry of `events`, `rq requeue --queue events --all` resends. RQ's own failure lines lose job arguments (`log._HideJobArguments`: API keys, targets).
- `emit` never raises (not an Event, queue down, build failing: logged). Making an `Event` with a wrong field raises where it is made (`extra="forbid"`, url only `/path` or http(s)); long title/text are cut (256 / 3000), never refused.

## Emit an event (anywhere: route, service, job, sweep)
```python
from ofmhelpers.features.events import Event, emit, emits

emit(
    Event(
        name="links.clicks",
        title="3,000 clicks",
        text="Bio link",
        url="/links",
        key=f"links.clicks:3000:{link.id}",
    )
)  # key = once only


@emits(
    lambda views, *a, **kw: (
        Event(name="accounts.views", title=f"{views} views today")
        if views >= 5000
        else None
    )
)  # None = nothing
def views_today(account_id: int) -> int: ...
```
- `name`: dotted, the producer's own. `title` first line, `text` rest (plain text), `url` a site path (made absolute from `APP_BASE_URL`; left out of the message without it) or an http(s) link, `key` = once per sender and target, counted only once sent.
- Emit after the DB write it reports: an event is queued when `emit` runs, not on commit.
- Wired (2026-11-06): `chatting.unanswered` (`features/chatting/wait_events.py`, every 10 min while fans wait over 5 min, and 5 min after each fan message if that fan still waits untold (`features/chatting/rechecks.py`); after a fresh read; Fanvue off for now) and `reports.daily.models|views|chatting` (`features/reports/daily.py`, 14:00 Warsaw), and `chatting.mass` (`features/reports/mass.py`, 2026-11-07: the moment a mass message is stored, every model that sent one since the last report, one line each, no count; it and the report's Chatting part share `replaces="chatting.mass"`, so the chat shows only the newest; the same list in one window goes out once).

## Add a sender
1. `senders/<name>/`: `client.py` (the service's calls through `utils.outbound`; errors as `SendError` with `retry_after` / `permanent`, never quoting a URL that holds a secret), `sender.py` (class with `name`, `targets()`, `pace_s(target)`, `send(target, event)`, see `base.Sender`), `fake.py` (wire fake by host, registered in `utils/outbound._routes` and both test conftests' fake lists), `models.py`. A target that is a secret (a webhook URL) gets a `redact.py` shape.
2. One entry in `senders()` (`__init__.py`).
3. Its settings group in `config/settings.py` (+ `config/__init__.py` property). No token/target set = `targets()` empty = skipped.
4. Contract test `tests/contract/test_<name>_contract.py` (fake always, real under `live`).
Nothing else changes: no queue, dispatch, event or producer edit.

## Telegram (`telegram/`)
- Bot API 10.3 (core.telegram.org/bots/api, read 2026-11-06): `POST https://api.telegram.org/bot<token>/<method>`, JSON body; answer `{ok, result}` or `{ok: false, error_code, description, parameters: {retry_after, migrate_to_chat_id}}`. `sendMessage` `text` 1-4096 chars; sent plain (no `parse_mode`: nothing to escape), `link_preview_options.is_disabled`. `getUpdates` refuses while a webhook is set (we never set one).
- Settings `TelegramSettings`: `OFM_TELEGRAM_BOT_TOKEN` (@BotFather `/newbot`), `OFM_TELEGRAM_CHAT_IDS` (comma list: a person who pressed Start, or a group with the bot, negative id), pace `OFM_TELEGRAM_GROUP_PACE_S` 3 (~20/min a group), `OFM_TELEGRAM_CHAT_PACE_S` 1.
- Refusals (`client._refusal`): 429 + `parameters.retry_after` -> wait; `parameters.migrate_to_chat_id` (group became a supergroup) -> sent to the new id, error log "put <id> in OFM_TELEGRAM_CHAT_IDS"; other 4xx (chat not found, bot kicked/blocked, wrong token) -> permanent; 5xx / network -> retry.
- One bot posts to the agency's group from prod (token + chat id in the server `.env`, 2026-11-06); dev has `OFM_EVENTS_SENDERS_OFF=telegram` so the group gets nothing twice. **Dev's `.env` holds the same token** (checked 2026-11-07 by hash), so dev must never read the bot either.
- **Asks** (2026-11-07): `money` (or `/money`, `/money@<bot>`) typed in a chat of `OFM_TELEGRAM_CHAT_IDS` is answered in that chat with Clicks / Rev of the last 24 hours (`features/reports/asks.py` `ASKS`: a new word = one entry; `daily.money_now`). `telegram/inbox.py` reads: getUpdates long polling (`OFM_TELEGRAM_ASK_WAIT_S` 10; the app's stop waits for a read in flight, so `ofmhelpers` has `stop_grace_period: 30s`, review 2026-11-07: Docker's 10 s default killed it about half the time), read position in Redis `telegram:updates:offset`, confirmed only after the asks are queued (one `answer` RQ job each on the default queue; its messages go out through `dispatch.deliver_to`, the bus's own paced and retried delivery, to that chat only); an ask older than `OFM_TELEGRAM_ASK_MAX_AGE_S` (300), from another chat, or a command for another bot (`/money@other_bot`; the bot's name from getMe, once) is not answered (another chat's id is logged: how a new chat id is found now, since `chats()` sees nothing once updates are read; an answered ask logs `telegram: 'money' asked`, never its chat). An unreadable update is skipped by its id (`client._update`), never read again. `money` = the last 24 hours as of the ask (owner 2026-11-08: "current stuff"): each link's clicks asked of its provider by the hour (`features/reports/hours.py`, `LinkProvider.hourly`: the current hour and the 23 before; two Oopsie calls per 200 links, two Bouncy calls per link; nothing synced or stored), subs and sales of the fan sites for the same 24 hours; a provider that cannot tell is named under the block ("Oopsie: clicks could not be read right now"). The 14:00 report keeps its own window (since the last report, its marks). Sweep `telegram asks`, only where telegram is not in `OFM_EVENTS_SENDERS_OFF` (prod). Telegram or Redis down: warning, the next read 20 s later (the sweep's own wait, `asks.pause_s`). **Privacy mode is on** for the bot (`getMe` `can_read_all_group_messages` false, 2026-11-07): in the group only `/money` reaches it; for a plain `money`, @BotFather `/setprivacy` -> Disable, then remove and add the bot to the group again.
- Find a chat id: write anything to the bot (or in a group with it): with asks read on prod, the log line `telegram: ... typed in chat <id>` shows it for an ask word; otherwise `TelegramClient(token).chats()` on a server that does not read (getUpdates keeps 24 h).
- The token is in every URL: client errors never quote the request (`from None`), and `redact.py` masks `<8-10 digits>:<35 chars>` in every span/log (Logfire's requests spans carry `http.url`; checked on dev: `bot[REDACTED]`).
- Text layout `message_text`: title / text / link, one per line, counted in UTF-16 units as Telegram counts (an emoji is 2); only the text is cut to fit, a title over 1024 too.
