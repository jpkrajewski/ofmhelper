"""Which tables `audit_log` records (alembic a1b2c3d4e5f8) and why every
other one is left out. A new table is one line in one of the two:
`tests/integration/db/test_audit.py` fails on a mapped table in neither,
and reads the triggers the database really has.

Audited: the rows people make and edit (`<table>_audit` + `<table>_stamp`
triggers, `db/models.py` `Timestamps` + `Authored`), and `jobs` for its
`hidden_at` alone (who hid a card)."""

# Audited in full (both triggers, both stamps).
AUDITED: frozenset[str] = frozenset(
    {
        "users",
        "user_identities",
        "user_api_keys",
        "user_cookies",
        "devices",
        "logins",
        "device_logins",
        "tracked_accounts",
        "creators",
        "watched_profile_groups",
        "short_links",
        "picture_presets",
        "self_link_domains",
        "clone_examples",
        "user_event_targets",
    }
)

# Audited for one column only: its trigger, no stamps.
AUDITED_COLUMNS: frozendict[str, str] = frozendict({"jobs": "hidden_at"})

EXCLUDED: frozendict[str, str] = frozendict(
    {
        "audit_log": "the log itself",
        "phone_revisions": "its own record of the phone tables, written by a trigger",
        "password_links": "one-time tokens; the link's use is the user's sign-in",
        "chat_threads": "copied from Fanvue / Stacked by the chat sync",
        "chat_fan_messages": "copied from Fanvue / Stacked by the chat sync",
        "chat_model_messages": "copied from Fanvue / Stacked by the chat sync",
        "chat_accounts": "the agency's chat logins; a connect is logged on its route",
        "chat_notifications": "events the platforms send",
        "chat_sales": "events the platforms send",
        "chat_webhooks": "the platforms' webhook registrations, made by the app",
        "report_runs": "the daily report's own bookkeeping",
        "downloads": "its job's record: owner, link, file, when used",
        "applications": "write-once leads from the public page",
        "watched_profiles": "/reels scans (retired): written by the scanner",
        "watched_profile_scans": "/reels scan history (retired)",
        "tracked_posts": "copied from the platforms by the account scans",
        "tracked_account_snapshots": "the account scans' own history",
        "drive_account_folders": "made and healed by the Drive sweep",
        "drive_va_folders": "made and renamed by the Drive sweep",
        "drive_uploads": "its own record: who, which file, which folder, when",
        "drive_posted_files": "what the Drive sweep saw in POSTED",
        "short_link_daily_stats": "copied from the link providers",
        "short_link_syncs": "the link sync's own bookkeeping",
        "self_link_clicks": "visitors' clicks",
        "clone_ai_checks": "its job's record (/clone-links, retired)",
        "clone_from_links": "its job's record (/clone-links, retired)",
    }
)
