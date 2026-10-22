"""Every colour of the site lives in one place: the palette at the top of
static/css/tokens.css (the app) or the `:root` token block at the top of
static/css/landing.css (the public landing page, its own dark look). Rules,
templates and scripts point at those tokens; a colour literal anywhere else
is a custom colour creeping back in.

Data colours are not design: the phone drawings (phones/render.py, from
the catalog) and photos are out of scope here."""

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
PALETTE = re.compile(r"^\s*--(paper|wash|line|grey|ink|red):\s*(#[0-9a-fA-F]{6});")
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


def test_palette_is_five_neutrals_and_one_red():
    values = [
        m.group(1)
        for line in TOKENS.read_text("utf-8").splitlines()
        if (m := PALETTE.match(line))
    ]
    assert values == ["paper", "wash", "line", "grey", "ink", "red"]


@pytest.mark.parametrize("path", APP_CSS, ids=lambda p: p.name)
def test_stylesheets_have_no_colour_outside_the_palette(path):
    assert _hits(path.read_text("utf-8")) == []


def test_landing_colours_live_in_its_token_block():
    text = LANDING_CSS.read_text("utf-8")
    assert text.lstrip().startswith((":root", "/*"))
    tokens_end = text.index("}", text.index(":root")) + 1
    assert _hits(text[tokens_end:]) == []


@pytest.mark.parametrize(
    "path",
    sorted(
        p
        for p in [
            *(WEB / "templates").rglob("*.html"),
            *(WEB / "static" / "js").glob("*.js"),
        ]
    ),
    ids=lambda p: p.name,
)
def test_templates_and_scripts_use_no_colour_of_their_own(path):
    assert _hits(path.read_text("utf-8")) == []
