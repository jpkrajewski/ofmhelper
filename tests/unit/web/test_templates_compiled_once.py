"""A template's file is not looked at on every render.

Jinja's auto_reload stats a template on every include/import: a 20-card
gallery is ~100 stats, ~200 ms on dev's Windows bind mount against 4 ms
without (2026-10-24: Hide / Show hidden felt slow). Outside production a
file is looked at once a second at most, so an edit still shows without a
restart; production never looks."""

import os

import jinja2.loaders
import pytest

from ofmhelpers.web import templates_config
from ofmhelpers.web.templates_config import TemplateFiles, get_templates


@pytest.fixture
def stats(monkeypatch) -> list[str]:
    """Every template file Jinja looks at for an edit."""
    seen: list[str] = []
    real = jinja2.loaders.os.path.getmtime

    def counting(path):
        seen.append(path)
        return real(path)

    monkeypatch.setattr(jinja2.loaders.os.path, "getmtime", counting)
    return seen


def _env(directory, auto_reload: bool = True) -> jinja2.Environment:
    return jinja2.Environment(
        loader=TemplateFiles(directory), auto_reload=auto_reload, autoescape=True
    )


def test_renders_within_a_second_never_look_at_the_files(stats):
    env = get_templates().env
    env.get_template("_gallery_hidden.html").render(hidden_count=1)
    stats.clear()
    for _ in range(20):
        env.get_template("_gallery_hidden.html").render(hidden_count=1)
    assert stats == []


def test_an_edit_shows_once_the_file_is_looked_at_again(tmp_path, monkeypatch, stats):
    page = tmp_path / "page.html"
    page.write_text("old", "utf-8")
    env = _env(tmp_path)
    assert env.get_template("page.html").render() == "old"

    page.write_text("new", "utf-8")
    old_mtime = page.stat().st_mtime
    os.utime(page, (old_mtime + 5, old_mtime + 5))
    assert env.get_template("page.html").render() == "old"  # not looked at yet
    monkeypatch.setattr(templates_config, "TEMPLATE_RECHECK_S", 0)
    assert env.get_template("page.html").render() == "new"
    assert stats


def test_production_never_looks(tmp_path, monkeypatch, stats):
    monkeypatch.setattr(templates_config, "TEMPLATE_RECHECK_S", 0)
    page = tmp_path / "page.html"
    page.write_text("x", "utf-8")
    env = _env(tmp_path, auto_reload=False)
    env.get_template("page.html").render()
    stats.clear()  # the first load reads it, once
    for _ in range(5):
        env.get_template("page.html").render()
    assert stats == []


def test_the_app_reloads_templates_only_outside_production(monkeypatch):
    get_templates.cache_clear()
    monkeypatch.setenv("OFM_ENV", "production")
    try:
        assert get_templates().env.auto_reload is False
        assert isinstance(get_templates().env.loader, TemplateFiles)
    finally:
        get_templates.cache_clear()
