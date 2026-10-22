"""Every colour of the site lives in one place: the palette at the top of
static/css/app.css. Rules, templates and scripts point at its tokens; a
colour literal anywhere else is a custom colour creeping back in.

Data colours are not design: the phone drawings (phones/render.py, from
the catalog) and photos are out of scope here."""

import re

import pytest
from repo_paths import SRC

WEB = SRC / "web"
CSS = WEB / "static" / "css" / "app.css"
PALETTE = re.compile(r"^\s*--(paper|wash|line|grey|ink|red):\s*(#[0-9a-fA-F]{6});")
COLOUR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab|lab|lch|hwb)\("
    r"|(?:color|background|border|fill|stroke|outline)[\w-]*\s*[:=]\s*\"?"
    r"(?:red|green|blue|purple|violet|orange|yellow|gold|pink|teal|cyan|gray|grey"
    r"|white|black|indigo|magenta|crimson|navy)\b",
    re.IGNORECASE,
)
# The public landing page is restyled on the palette in its own revision
# (R11 of the UI plan); until then it is the one exempt file.
EXEMPT = {"reachmodel.html"}


def _hits(text: str) -> list[str]:
    return [
        f"{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        if COLOUR.search(line) and not PALETTE.match(line)
    ]


def test_palette_is_five_neutrals_and_one_red():
    values = [
        m.group(1)
        for line in CSS.read_text("utf-8").splitlines()
        if (m := PALETTE.match(line))
    ]
    assert values == ["paper", "wash", "line", "grey", "ink", "red"]


def test_stylesheet_has_no_colour_outside_the_palette():
    assert _hits(CSS.read_text("utf-8")) == []


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
