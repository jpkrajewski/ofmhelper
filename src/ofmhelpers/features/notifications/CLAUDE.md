# features/notifications

Every notification on the website, one file: `service.py` (owner 2026-11-20: "one notification system for chatting and all of notifications on the website", "use websockets").

```
something changes ──> changed(user_id | None)          Redis PUBLISH "notifications"
                                                      (None = the chats: every chat reader)
web process: ONE subscription (_Changes) ──> a queue per open socket
browser ──WS /notifications/ws──> updates(viewer)      read(viewer) at once,
                                                      again when a change is theirs
read(viewer) = chats (if opens(viewer, CHATTING)) + their own finished /threads, /trials jobs,
               newest first by `at`
```
- Nothing stored for it: every line is read from what the app keeps (chats store, jobs table). `at` = when it happened: a chat event's own time, a wait's latest fan + `OFM_CHATTING_UNANSWERED_AFTER_S`, a job's `finished_at`, a dead login now. Shown for `OFM_CHATTING_NOTIFICATION_SHOWN_S` (a day).
- Who calls `changed()`: `features/jobs/runner.run_job` when a job ends (its person); chats: every webhook (`chatting/webhooks.receive`), every platform round (`chatting/sync.run_when_due`), the 10-min look and the 5-min recheck (`chatting/wait_events`, before their Telegram gate).
- A new kind of notification: one more source function in `service.py` (returns `Notification`s with their `at`), added in `read`, and a `changed()` where it happens.
- Redis: one connection per web process while any socket is open (`_Changes`: made with the first socket, dropped with the last, made again 3 s after Redis is lost), not one per tab. A burst of changes while a list is being read is read once.
- Web: `web/routers/notifications.py` (the WebSocket). Sign-in: `web/dependencies.SignedIn` hands `updates` the HTTP gate's own check (`AuthMiddleware.socket_viewer`, since the middleware never sees a WebSocket); it runs at connect and again before every push, so a person deactivated, logged out, idle or no longer allowed the chats is closed with 1008 at the next change, and `notifications.js` does not reconnect on 1008 (any other close: 3 s .. 60 s). `web/sockets.stream` sends until the browser leaves. Jobs show by when they finished (`finished_at`), not when they were queued. `ui.notifications_menu` + `static/js/notifications.js` (seen / chime / desktop per `event_ids` in localStorage). nginx passes `Upgrade` (nginx/ofmhelpers.conf, on prod since before 2026-11-20); uvicorn pings every 20 s, under nginx's 600 s read timeout.
- Telegram is not here: `features/events` + `integrations/senders`.
