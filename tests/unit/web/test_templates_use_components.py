"""Pages build their UI from components/ui.html, never from hand-written
markup with the components' own classes: the look of a button, notice,
badge, empty state or page header lives in one macro (and components.css), so a
change there reaches every page. Also no inline styles beyond CSS custom
properties carrying data (a chart point's position)."""

import re

import pytest
from repo_paths import SRC

TEMPLATES = SRC / "web" / "templates"
# Classes only components/ui.html may write.
OWNED = {
    "btn", "btn-primary", "btn-outline", "btn-ghost", "btn-danger", "btn-block",
    "icon-btn", "ph-icon-btn",
    "notice", "notice-ok", "notice-warn", "notice-fail",
    "badge", "badge-neutral", "badge-ok", "badge-warn", "badge-fail",
    "empty-state", "page-head", "page-head-actions", "card", "card-title",
    "stat", "stat-label", "stat-value", "stat-note",
    "table-wrap", "menu", "menu-list", "menu-item", "menu-item-danger",
}  # fmt: skip
CLASS_ATTR = re.compile(r'class="([^"]*)"')
STYLE_ATTR = re.compile(r'style="(?!--)')


def _pages():
    return sorted(p for p in TEMPLATES.rglob("*.html") if p.parent.name != "components")


def _problems(text: str) -> list[str]:
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for value in CLASS_ATTR.findall(line):
            # Tokens of the literal value and of quoted strings in Jinja.
            tokens = set(re.findall(r"[\w-]+", value))
            if owned := tokens & OWNED:
                found.append(f"{n}: {sorted(owned)}")
        if STYLE_ATTR.search(line) or "<style" in line:
            found.append(f"{n}: inline style")
    return found


@pytest.mark.parametrize("path", _pages(), ids=lambda p: p.name)
def test_page_uses_components_not_their_classes(path):
    assert _problems(path.read_text("utf-8")) == []
