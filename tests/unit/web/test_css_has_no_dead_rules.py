import re

from repo_paths import SRC

WEB = SRC / "web"
CSS = [
    WEB / "static" / "css" / n
    for n in ("tokens.css", "base.css", "components.css", "pages.css")
]
DYNAMIC = ("badge-", "notice-", "sg-", "ref-mark-")


def test_every_styled_class_is_used():
    css = re.sub(
        r"/\*.*?\*/", "", "".join(p.read_text("utf-8") for p in CSS), flags=re.DOTALL
    )
    css = re.sub(r"url\([^)]*\)", "", css)
    used = " ".join(
        p.read_text("utf-8")
        for p in [
            *(WEB / "templates").rglob("*.html"),
            *(WEB / "static" / "js").glob("*.js"),
        ]
    )
    classes = set(re.findall(r"\.([a-z][a-z0-9-]*)", css))
    dead = sorted(
        c
        for c in classes
        if not c.startswith(DYNAMIC)
        and not re.search(rf"(?<![\w-]){re.escape(c)}(?![\w-])", used)
    )
    assert dead == []
