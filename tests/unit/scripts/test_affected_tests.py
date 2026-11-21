import importlib.util
import itertools
import sys

from repo_paths import REPO

sys.path.insert(0, str(REPO / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "affected_tests", REPO / "scripts/affected_tests.py"
)
assert _spec is not None
assert _spec.loader is not None
affected_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(affected_tests)


def test_a_nav_change_runs_the_nav_and_chrome_tests():
    picked = affected_tests.affected(["src/ofmhelpers/web/nav.py"])
    assert "tests/integration/web/test_nav.py" in picked
    assert "tests/integration/web/test_page_chrome.py" in picked


def test_the_sweeps_and_a_page_script_reach_their_tests():
    picked = affected_tests.affected(
        ["src/ofmhelpers/background/sweeps.py", "src/ofmhelpers/web/static/js/edit.js"]
    )
    assert "tests/unit/web/test_sweeps.py" in picked
    assert "tests/e2e/test_edit_page.py" in picked


def test_a_changed_test_file_runs_itself_and_an_unknown_file_runs_nothing():
    assert affected_tests.affected(["tests/unit/scripts/test_self_learn.py"]) == [
        "tests/unit/scripts/test_self_learn.py"
    ]
    assert affected_tests.affected(["README.md", "tests/missing/test_x.py"]) == []


def test_every_path_in_the_map_exists():
    links = affected_tests.AffectedMap.model_validate_json(
        affected_tests.MAP.read_text(encoding="utf-8")
    )
    missing = [r for link in links.links for r in link.run if not (REPO / r).is_file()]
    assert missing == []


def test_a_script_runs_its_own_test_file():
    assert "tests/unit/scripts/test_self_learn.py" in affected_tests.affected(
        ["scripts/self_learn.py"]
    )
    assert "tests/unit/scripts/test_test_sh.py" in affected_tests.affected(
        ["scripts/test.sh"]
    )


def test_a_hook_or_a_guard_runs_the_guard_tests():
    for changed in [".claude/hooks/guard.py", ".claude/hooks/guards/git.py"]:
        assert "tests/unit/hooks/test_guards.py" in affected_tests.affected([changed])


def test_dependents_follow_imports_transitively_up_to_the_depth_cap():
    chain = ["a", "b", "c", "d", "e"]
    edges = [
        affected_tests.Edge(source=s, target=t, lazy=False)
        for s, t in itertools.pairwise(chain)
    ]
    assert affected_tests.dependents("c", edges) == {"a", "b", "c"}
    assert affected_tests.dependents("e", edges) == {"b", "c", "d", "e"}


def test_a_selection_of_most_of_the_suite_says_so(monkeypatch, capsys):
    everything = sorted(
        p.relative_to(REPO).as_posix() for p in (REPO / "tests").rglob("test_*.py")
    )
    monkeypatch.setattr(affected_tests, "affected", lambda _: everything)
    affected_tests.main(["src/ofmhelpers/config/settings.py"])
    captured = capsys.readouterr()
    assert "most of the suite" in captured.err
    assert captured.out.splitlines() == everything
