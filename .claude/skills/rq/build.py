"""Fetch RQ's own docs for one release, one file per page, into `docs/`.

RQ and its docs are BSD-licensed (rq/rq `LICENSE`): committed, source in SKILL.md.
Run (see SKILL.md): python build.py v3.0     (the tag of the version in uv.lock)
"""

import json
import sys
import urllib.request
from pathlib import Path

PAGES = "https://api.github.com/repos/rq/rq/contents/docs/docs?ref={tag}"
HERE = Path(__file__).parent


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 (fixed https URLs)
        return response.read().decode("utf-8")


def main(tag: str) -> None:
    docs = HERE / "docs"
    docs.mkdir(exist_ok=True)
    pages = [
        page
        for page in json.loads(get(PAGES.format(tag=tag)))
        if page["name"].endswith(".md")
    ]
    for page in pages:
        (docs / page["name"]).write_text(get(page["download_url"]), "utf-8")
    (HERE / "VERSION").write_text(f"{tag}\n", "utf-8")
    print(f"{len(pages)} pages of RQ {tag} -> {docs}")


if __name__ == "__main__":
    main(sys.argv[1])
