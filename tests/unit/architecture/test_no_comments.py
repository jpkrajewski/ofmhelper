import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "check_no_comments", ROOT / "scripts" / "check_no_comments.py"
)
assert SPEC is not None
assert SPEC.loader is not None
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def test_the_whole_repo_has_no_comments_or_docstrings():
    assert check.problems(ROOT, check.every_file(ROOT)) == []


def test_the_check_sees_comments_and_docstrings():
    python = 'def f():\n    """doc"""\n    x = 1  # why\n    return 2  # noqa: E501\n'
    script = "a = 1; // x\nb = '//not' /* y */\nc = /ab\\/c/g;\n"
    style = "a { color: red; } /* x */\nb { background: url(//cdn) }\n"
    template = "{# x #}{# djlint:off #}\n<!-- y -->"

    assert check.problem_lines("src/m.py", python) == [1, 3]
    assert check.problem_lines("src/ofmhelpers/web/static/js/a.js", script) == [1, 2]
    assert check.problem_lines("src/ofmhelpers/web/static/css/a.css", style) == [1]
    assert check.problem_lines("src/ofmhelpers/web/templates/a.html", template) == [
        1,
        2,
    ]
    assert check.problem_lines("tests/fixtures/saved_page.html", template) == []
