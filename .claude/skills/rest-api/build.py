"""Fetch Zalando's RESTful API Guidelines and keep the MUST rules, one file each.

Writes `must.md` (id | chapter | title) and `rules/<id>.md` (the rule's text).
Zalando's text is CC BY 4.0: committed, attribution in SKILL.md.
Run (see SKILL.md): python build.py
"""

import itertools
import json
import re
import urllib.request
from pathlib import Path

REPO = "zalando/restful-api-guidelines"
CHAPTERS = f"https://api.github.com/repos/{REPO}/contents/chapters"
HERE = Path(__file__).parent
RULE = re.compile(r"^\[#(\d+)\]\n=+ \{(\w+)\}(.*)$", re.MULTILINE)
CHAPTER = re.compile(r"^= (.+)$", re.MULTILINE)


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 (fixed https URLs)
        return response.read().decode("utf-8")


def main() -> None:
    rules = HERE / "rules"
    rules.mkdir(exist_ok=True)
    rows = []
    for entry in json.loads(get(CHAPTERS)):
        text = get(entry["download_url"])
        chapter = CHAPTER.search(text)
        found = list(RULE.finditer(text))
        for match, following in itertools.pairwise([*found, None]):
            number, level, title = match[1], match[2], match[3].strip(" /")
            if level != "MUST":
                continue
            body = text[
                match.end() : following.start() if following else len(text)
            ].strip()
            (rules / f"{number}.md").write_text(
                f"# [{number}] MUST {title}\n\n{body}\n", "utf-8"
            )
            rows.append(
                f"{number} | {chapter[1] if chapter else entry['name']} | {title}"
            )
    (HERE / "must.md").write_text("\n".join(rows) + "\n", "utf-8")
    print(f"{len(rows)} MUST rules -> {rules}")


if __name__ == "__main__":
    main()
