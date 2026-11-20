import re

import pytest
from repo_paths import SRC

WEB = SRC / "web"
CSS_DIR = WEB / "static" / "css"
TOKENS = CSS_DIR / "tokens.css"
APP_CSS = [
    CSS_DIR / n for n in ("tokens.css", "base.css", "components.css", "pages.css")
]
LANDING_CSS = CSS_DIR / "landing.css"
PALETTE = re.compile(
    r"^\s*--(paper|wash|line|grey|ink|accent-hue|accent-hue-2|red):\s*(#[0-9a-fA-F]{6});"
)
COLOUR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab|lab|lch|hwb)\("
    r"|(?:color|background|border|fill|stroke|outline)[\w-]*\s*[:=]\s*\"?"
    r"(?:red|green|blue|purple|violet|orange|yellow|gold|pink|teal|cyan|gray|grey"
    r"|white|black|indigo|magenta|crimson|navy)\b",
    re.IGNORECASE,
)


def _hits(text: str) -> list[str]:
    return [
        f"{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        if COLOUR.search(line) and not PALETTE.match(line)
    ]


def test_palette_is_five_neutrals_one_accent_and_one_red_per_theme():
    text = TOKENS.read_text("utf-8")
    dark = text.index(':root[data-theme="dark"]')
    slots = [
        "paper",
        "wash",
        "line",
        "grey",
        "ink",
        "accent-hue",
        "accent-hue-2",
        "red",
    ]
    for block in (text[:dark], text[dark:]):
        values = [
            m.group(1) for line in block.splitlines() if (m := PALETTE.match(line))
        ]
        assert values == slots


def _without_dark_theme(text: str) -> str:
    if ':root[data-theme="dark"]' not in text:
        return text
    start = text.index(':root[data-theme="dark"]')
    return text[:start] + text[text.index("}", start) + 1 :]


@pytest.mark.parametrize("path", APP_CSS, ids=lambda p: p.name)
def test_stylesheets_have_no_colour_outside_the_palette(path):
    assert _hits(_without_dark_theme(path.read_text("utf-8"))) == []


def test_the_dark_theme_only_sets_tokens():
    text = TOKENS.read_text("utf-8")
    start = text.index(':root[data-theme="dark"]')
    body = text[text.index("{", start) + 1 : text.index("}", start)]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    declarations = [d.strip() for d in body.split(";") if d.strip()]
    assert declarations
    assert all(d.startswith(("--", "color-scheme:")) for d in declarations)


def test_landing_colours_live_in_its_token_block():
    text = LANDING_CSS.read_text("utf-8")
    assert text.lstrip().startswith((":root", "/*"))
    tokens_end = text.index("}", text.index(":root")) + 1
    assert _hits(text[tokens_end:]) == []


EXEMPT = {"bridge.html"}


@pytest.mark.parametrize(
    "path",
    sorted(
        p
        for p in [
            *(WEB / "templates").rglob("*.html"),
            *(WEB / "static" / "js").glob("*.js"),
        ]
        if p.name not in EXEMPT
    ),
    ids=lambda p: p.name,
)
def test_templates_and_scripts_use_no_colour_of_their_own(path):
    assert _hits(path.read_text("utf-8")) == []
