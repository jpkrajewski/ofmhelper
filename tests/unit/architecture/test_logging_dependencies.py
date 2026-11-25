# Copyright (C) 2026 jpkrajewski

import ast
lazy from pathlib import Path

from repo_paths import REPO


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_application_modules_use_stdlib_loggers_directly():
    offenders = []
    paths = (REPO / "src" / "ofmhelpers").rglob("*.py")
    paths = [*paths, *(REPO / "alembic").rglob("*.py")]
    for path in paths:
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.ImportFrom) or node.module != "ofmhelpers.log":
                continue
            if any(alias.name == "get_logger" for alias in node.names):
                offenders.append(str(path.relative_to(REPO)))

    assert offenders == []


def test_logging_bootstrap_does_not_import_application_configuration():
    path = REPO / "src" / "ofmhelpers" / "log.py"
    imported = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    assert not any(name.startswith("ofmhelpers") for name in imported)


def test_alembic_uses_the_json_bootstrap():
    tree = _tree(REPO / "alembic" / "env.py")
    calls = {
        ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)
    }

    assert "configure_logging" in calls
    assert "fileConfig" not in calls


def test_logging_is_not_an_application_settings_group():
    settings_path = REPO / "src" / "ofmhelpers" / "config" / "settings.py"
    config_path = REPO / "src" / "ofmhelpers" / "config" / "__init__.py"
    settings_classes = {
        node.name
        for node in ast.walk(_tree(settings_path))
        if isinstance(node, ast.ClassDef)
    }
    config_source = config_path.read_text(encoding="utf-8")

    assert "LoggingSettings" not in settings_classes
    assert "LoggingSettings" not in config_source
