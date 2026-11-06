# integrations/senders

Where events go out (Telegram, later Discord, email, ...). Any part of the app emits an `Event`; this package only delivers it.

```
emit(Event) | @emits(build)          features/events/bus.py     returns at once, never raises
  -> `events` queue                   compose events-worker, 1 process, HIGHEST priority
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
- Wired (2026-11-06): `chatting.unanswered` (`features/chatting/wait_events.py`, every 15 min while fans wait over 5 min, after a fresh read; Fanvue off for now) and `reports.daily.models|views|chatting` (`features/reports/daily.py`, 14:00 Warsaw).

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
- One bot posts to the agency's group from prod (token + chat id in the server `.env`, 2026-11-06); dev has `OFM_EVENTS_SENDERS_OFF=telegram` so the group gets nothing twice.
- Find a chat id: press Start on the bot (or add it to a group and write there), then `TelegramClient(token).chats()` (getUpdates keeps 24 h).
- One bot per server: dev and prod each their own token.
- The token is in every URL: client errors never quote the request (`from None`), and `redact.py` masks `<8-10 digits>:<35 chars>` in every span/log (Logfire's requests spans carry `http.url`; checked on dev: `bot[REDACTED]`).
- Text layout `message_text`: title / text / link, one per line, counted in UTF-16 units as Telegram counts (an emoji is 2); only the text is cut to fit, a title over 1024 too.
