"""Download the AWS DevOps Guidance PDF and split it: one file per indicator.

Writes `index.md` (committed: id | category | title | page slug) and
`cache/<ID>.md` (gitignored: AWS's text, all rights reserved, never commit).
Run (see SKILL.md): uv run --no-project --with pypdf python build_cache.py
"""

import io
import re
import unicodedata
import urllib.request
from pathlib import Path

from pypdf import PdfReader

PDF_URL = "https://docs.aws.amazon.com/pdfs/wellarchitected/latest/devops-guidance/devops-guidance.pdf"
HERE = Path(__file__).parent
HEADING = re.compile(
    r"^\[([A-Z]+\.[A-Z]+\.\d+)\] (.+(?:\n.+)?)\nCategory: ([A-Z]+)", re.MULTILINE
)
NOISE = re.compile(
    r"^(DevOps Guidance AWS Well-Architected|=== page \d+ ===)\n", re.MULTILINE
)


def main() -> None:
    with urllib.request.urlopen(PDF_URL, timeout=120) as response:
        reader = PdfReader(io.BytesIO(response.read()))
    text = "".join(
        f"=== page {n} ===\n{page.extract_text() or ''}\n"
        for n, page in enumerate(reader.pages, 1)
    )
    text = NOISE.sub(
        "", unicodedata.normalize("NFKC", text)
    )  # no-break spaces, "fi" ligatures: grep-able
    slugs = {
        name.split("-", 1)[0].upper(): name
        for name in reader.named_destinations
        if "-" in name
    }
    cache = HERE / "cache"
    cache.mkdir(exist_ok=True)
    found = list(HEADING.finditer(text))
    rows = []
    for match, following in zip(found, [*found[1:], None], strict=True):
        indicator, title, category = match[1], " ".join(match[2].split()), match[3]
        body = text[match.end() : following.start() if following else len(text)].strip()
        (cache / f"{indicator}.md").write_text(
            f"# [{indicator}] {title}\nCategory: {category}\n\n{body}\n", "utf-8"
        )
        rows.append(f"{indicator} | {category} | {title} | {slugs.get(indicator, '')}")
    (HERE / "index.md").write_text("\n".join(rows) + "\n", "utf-8")
    print(f"{len(rows)} indicators -> {cache}")


if __name__ == "__main__":
    main()
