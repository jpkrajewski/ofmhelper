"""
ofmhelpers/web/recovery.py

Background recovery sweeper: every few minutes, sweep kie.ai's task log for
generations that were created but never downloaded (the in-request poll
timed out, the server restarted mid-generation, ...) and pull them down to
the server automatically. This is what makes a poll timeout a non-event --
nobody has to log into kie.ai and check manually.

Runs with the API keys configured in the environment (admin first, then VA).
A task created with a key the sweeper doesn't have simply stays pending and
is retried next sweep; KieAIClient's resolved-log bookkeeping guarantees
each terminal task is only ever handled once, and its age cutoff guarantees
nothing is re-checked forever.
"""

import asyncio
import traceback

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.config import settings

SWEEP_INTERVAL_S = settings.web.recovery_sweep_interval_s


def _configured_keys() -> list[str]:
    s = settings.web
    keys = []
    for key in (s.kie_ai_api_key_admin, s.kie_ai_api_key_va):
        key = (key or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def run_recovery_once() -> list[dict]:
    """One sweep across all configured keys. Blocking (requests) -- call via
    asyncio.to_thread from the event loop."""
    recovered = []
    for key in _configured_keys():
        try:
            recovered += KieAIClient.from_env(api_key=key).resume_pending()
        except Exception:
            # A broken sweep must never take the loop down -- log and move on.
            traceback.print_exc()
    return recovered


async def recovery_loop() -> None:
    while True:
        await asyncio.to_thread(run_recovery_once)
        await asyncio.sleep(SWEEP_INTERVAL_S)
