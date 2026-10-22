"""Runs one SQL query against the app's Logfire project and prints CSV.

The first place to look when debugging dev or prod (CLAUDE.md "Debugging"):
every request, job, SQL query, outbound HTTP call and log line is in
Logfire's `records` table -- no ssh, no psql.

    C="docker compose -f docker-compose.yml -f docker-compose.dev.yml"
    MSYS_NO_PATHCONV=1 $C run --rm --no-deps -v ~/.logfire:/lfhome/.logfire \
        tests python scripts/logfire_query.py "SELECT ... FROM records ..."

The read token comes from `LOGFIRE_READ_TOKEN` or the file
`logfire read-tokens ... create --save` wrote (~/.logfire/project/.logfire/
read_token.json, mounted at /lfhome); it is never printed. Region EU.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_API = "https://logfire-eu.pydantic.dev/v1/query"
_TOKEN_FILES = (
    Path("/lfhome/.logfire/project/.logfire/read_token.json"),
    Path.home() / ".logfire/project/.logfire/read_token.json",
)


def _token() -> str:
    if token := os.environ.get("LOGFIRE_READ_TOKEN"):
        return token
    for path in _TOKEN_FILES:
        if path.is_file():
            return json.loads(path.read_text())["token"]
    msg = (
        "No Logfire read token: set LOGFIRE_READ_TOKEN or run `logfire --region eu"
        " read-tokens --project jakubpkrajewski/starter-project create --save`"
    )
    raise SystemExit(msg)


def query(sql: str) -> str:
    """The rows of `sql` as CSV."""
    request = urllib.request.Request(  # noqa: S310 -- fixed https URL
        f"{_API}?{urllib.parse.urlencode({'sql': sql})}",
        headers={"Authorization": f"Bearer {_token()}", "Accept": "text/csv"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 -- fixed https URL
        return response.read().decode()


if __name__ == "__main__":
    if len(sys.argv[1:]) != 1:
        raise SystemExit(__doc__)
    print(query(sys.argv[1]))
