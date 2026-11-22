import json
import sys
import urllib.request
from pathlib import Path

PAGES = (
    "https://api.github.com/repos/pydantic/pydantic/contents/docs/concepts?ref={tag}"
)
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
    print(f"{len(pages)} pages of Pydantic {tag} -> {docs}")


if __name__ == "__main__":
    main(sys.argv[1])
