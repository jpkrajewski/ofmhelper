# Copyright (C) 2026 jpkrajewski

import re

import pytest
from repo_paths import SRC

TEMPLATES = SRC / "web" / "templates"
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
HAND_BUTTON = re.compile(r'class="download-btn"')
STYLE_ATTR = re.compile(r'style="(?!--)')


EXEMPT = {"bridge.html"}


def _pages():
    return sorted(
        p
        for p in TEMPLATES.rglob("*.html")
        if p.parent.name != "components" and p.name not in EXEMPT
    )


def _problems(text: str) -> list[str]:
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for value in CLASS_ATTR.findall(line):
            tokens = set(re.findall(r"[\w-]+", value))
            if owned := tokens & OWNED:
                found.append(f"{n}: {sorted(owned)}")
        if STYLE_ATTR.search(line) or "<style" in line:
            found.append(f"{n}: inline style")
        if HAND_BUTTON.search(line):
            found.append(f"{n}: hand-written button")
    return found


@pytest.mark.parametrize("path", _pages(), ids=lambda p: p.name)
def test_page_uses_components_not_their_classes(path):
    assert _problems(path.read_text("utf-8")) == []
