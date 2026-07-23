"""
ofmhelpers/aigenproviders/kaiai/upload_cache.py

In-memory LRU cache mapping "already uploaded to kie.ai" local files to
their hosted URL, so the same reference file -- reference images/videos/
audio routinely get reused job after job via the /generate reuse pickers --
isn't re-uploaded to kie.ai's tempfile host every single time.

Deliberately NOT persisted to disk: this is a per-process optimization, not
a source of truth. Lost on restart just means the next use of a file
re-uploads and repopulates the cache -- never a correctness problem, only
ever a missed optimization. Capped at MAX_ENTRIES; oldest
(least-recently-used) entry is evicted once full.

Cache key is (api_key, local_path), not path alone: kie.ai's tempfile host
namespaces uploads per account (the "kieai/<account-id>/refs/..." segment in
every downloadUrl), so a URL uploaded under one API key is not guaranteed
valid -- or even the right file -- under a different key. This app hands
out two keys (admin/VA) that may well reference the same local file, so
keying on path alone would risk handing one account's uploaded-file
reference to a job running under a different account's key.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

MAX_ENTRIES = 100


class UploadCache:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, api_key: str, path: str) -> str | None:
        key = (api_key, path)
        with self._lock:
            url = self._entries.get(key)
            if url is not None:
                self._entries.move_to_end(key)  # mark as most-recently-used
            return url

    def put(self, api_key: str, path: str, url: str) -> None:
        key = (api_key, path)
        with self._lock:
            self._entries[key] = url
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)  # evict least-recently-used

    def discard(self, api_key: str, path: str) -> None:
        with self._lock:
            self._entries.pop((api_key, path), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# One process-wide cache, shared by every KieAIClient instance -- reference
# files get reused across different generations/jobs regardless of which
# client object happens to be handling the current one.
upload_cache = UploadCache()
