# noqa: CPY001

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from repo_paths import REPO

fake_docker = """#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_COMMANDS"
case "$*" in
  *"quality_metrics.py combine"*)
    python scripts/quality_metrics.py combine reports/.quality-work
    exit $?
    ;;
  *"quality_metrics.py ."*)
    if [[ "${FAKE_CUSTOM_EXIT:-0}" -ne 0 ]]; then
      printf 'custom analyzer error\n' >&2
    fi
    {
      printf '%s\n' '{'
      printf '%s\n' '  "callables": [],'
      printf '%s\n' '  "classes": [],'
      printf '%s\n' '  "dependencies": {"modules": [], "areas": []},'
      printf '%s\n' '  "maintainability": []'
      printf '%s\n' '}'
    } > reports/.quality-work/custom-metrics.json
    if [[ "${FAKE_SKIP_SUMMARY:-0}" -eq 0 ]]; then
      {
        printf '%s\n' '{'
        printf '%s\n' '  "counts": {"files": 0},'
        printf '%s\n' '  "offenders": {},'
        printf '%s\n' '  "dependency_areas": []'
        printf '%s\n' '}'
      } > reports/.quality-work/summary.json
    fi
    exit "${FAKE_CUSTOM_EXIT:-0}"
    ;;
  *"radon cc"*) printf '{}\n'; exit "${FAKE_RADON_CC_EXIT:-0}" ;;
  *"radon raw"*)
    if [[ "${FAKE_RADON_RAW_MALFORMED:-0}" -ne 0 ]]; then
      printf 'not json\n'
    else
      printf '{}\n'
    fi
    exit "${FAKE_RADON_RAW_EXIT:-0}"
    ;;
  *"radon mi"*) printf '{}\n'; exit "${FAKE_RADON_MI_EXIT:-0}" ;;
  *"pylint"*) printf '[]\n'; exit "${FAKE_PYLINT_EXIT:-0}" ;;
  *"xenon"*) printf 'xenon output\n'; exit "${FAKE_XENON_EXIT:-0}" ;;
  *"lint-imports"*) printf 'contracts output\n'; exit "${FAKE_IMPORT_EXIT:-0}" ;;
esac
exit 0
"""


class Project:
    def __init__(self, root: Path):
        self.root = root
        bin_dir = root.parent / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(fake_docker, encoding="utf-8")
        docker.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "FAKE_COMMANDS": str(root.parent / "commands"),
            },
        )
        (root / "scripts").mkdir(parents=True)
        shutil.copy(REPO / "scripts/quality.sh", root / "scripts/quality.sh")
        shutil.copy(
            REPO / "scripts/quality_metrics.py",
            root / "scripts/quality_metrics.py",
        )

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "scripts/quality.sh"],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture
def project(tmp_path) -> Project:
    root = tmp_path / "project"
    root.mkdir()
    return Project(root)


def test_stdout_and_saved_result_are_one_complete_json(project):
    result = project.run()

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert result.stderr == ""
    assert payload["passed"] is True
    assert set(payload["tools"]) == {
        "radon_cc",
        "radon_raw",
        "radon_mi",
        "xenon",
        "pylint",
        "import_linter",
        "custom_metrics",
    }
    assert payload["tools"]["radon_cc"]["results"] == {}
    assert payload["tools"]["custom_metrics"]["results"]["dependencies"] == {
        "modules": [],
        "areas": [],
    }
    assert payload["summary"]["counts"] == {"files": 0}
    saved = project.root / "reports/quality/results.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == payload
    commands = Path(project.env["FAKE_COMMANDS"]).read_text(encoding="utf-8")
    assert "docker-compose.yml" in commands
    assert "docker-compose.dev.yml" in commands
    assert commands.count("src tests alembic") >= 5


def test_xenon_and_import_failures_still_return_all_json_and_fail(project):
    project.env["FAKE_XENON_EXIT"] = "1"
    project.env["FAKE_IMPORT_EXIT"] = "1"

    result = project.run()

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert payload["tools"]["xenon"]["passed"] is False
    assert payload["tools"]["import_linter"]["passed"] is False
    assert payload["tools"]["custom_metrics"]["exit_code"] == 0


def test_pylint_duplicate_findings_do_not_fail(project):
    project.env["FAKE_PYLINT_EXIT"] = "8"

    result = project.run()

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["tools"]["pylint"]["exit_code"] == 8
    assert payload["tools"]["pylint"]["passed"] is True


def test_other_pylint_failure_fails_with_json(project):
    project.env["FAKE_PYLINT_EXIT"] = "1"

    result = project.run()

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["tools"]["pylint"]["passed"] is False
    assert payload["passed"] is False


def test_custom_analyzer_failure_includes_its_error_output(project):
    project.env["FAKE_CUSTOM_EXIT"] = "1"

    result = project.run()

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["tools"]["custom_metrics"]["passed"] is False
    assert payload["tools"]["custom_metrics"]["output"] == "custom analyzer error\n"


def test_malformed_tool_json_fails_even_when_tool_exit_is_zero(project):
    project.env["FAKE_RADON_RAW_MALFORMED"] = "1"

    result = project.run()

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["tools"]["radon_raw"]["exit_code"] == 0
    assert payload["tools"]["radon_raw"]["passed"] is False
    assert payload["passed"] is False


def test_missing_summary_fails_the_command(project):
    project.env["FAKE_SKIP_SUMMARY"] = "1"

    result = project.run()

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "error" in payload["summary"]
    assert payload["passed"] is False
