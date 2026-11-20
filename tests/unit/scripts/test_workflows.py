import re
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field
from repo_paths import REPO

WORKFLOWS = sorted((REPO / ".github/workflows").glob("*.yml"))

NODE24_MAJOR = {
    "actions/checkout": 5,
    "astral-sh/setup-uv": 7,
    "docker/setup-buildx-action": 4,
    "docker/build-push-action": 7,
    "biomejs/setup-biome": 2,
}
RUNNER = "ubuntu-24.04"


class Step(BaseModel):
    model_config = ConfigDict(extra="allow")

    uses: str | None = None


class Job(BaseModel):
    model_config = ConfigDict(extra="allow")

    runs_on: str = Field(alias="runs-on")
    steps: list[Step] = []


class Workflow(BaseModel):
    jobs: dict[str, Job]


def _load(path: Path) -> Workflow:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Workflow.model_validate({"jobs": data["jobs"]})


def test_there_are_workflows():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "deploy.yml", "promote.yml"}


def test_a_promote_pr_starts_no_ci_run_and_main_still_gates_the_deploy():
    ci = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    on = ci[True]
    assert on["pull_request"]["branches"] == ["dev"]
    assert on["push"]["branches"] == ["dev", "main"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_names_its_runner_image(path: Path):
    for name, job in _load(path).jobs.items():
        assert job.runs_on == RUNNER, f"{path.name}:{name} runs on {job.runs_on}"


_USES = re.compile(r"^\s*(?:-\s*)?uses:\s*([^@\s]+)@(\S+)(?:\s+#\s*(\S+))?\s*$")
_SHA = re.compile(r"[0-9a-f]{40}")
_VERSION = re.compile(r"v(\d+)(\.\d+){0,2}")


def _uses(path: Path) -> list[tuple[str, str, str | None]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [(m[1], m[2], m[3]) for line in lines if (m := _USES.match(line))]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_on_a_node24_major(path: Path):
    uses = _uses(path)
    steps = [s for job in _load(path).jobs.values() for s in job.steps if s.uses]
    assert len(uses) == len(steps), f"{path.name}: a `uses:` line was not read"
    for action, ref, comment in uses:
        assert action in NODE24_MAJOR, f"{path.name}: vet {action} first"
        version = comment if _SHA.fullmatch(ref) else ref
        major = _VERSION.fullmatch(version or "")
        assert major, f"{path.name}: {action}@{ref} names no version tag"
        assert int(major.group(1)) >= NODE24_MAJOR[action], f"{action}@{version}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit(path: Path):
    for action, ref, _ in _uses(path):
        assert _SHA.fullmatch(ref), f"{path.name}: {action}@{ref} is a tag"


def _ci() -> dict:
    return yaml.safe_load(
        (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )


def test_ci_passed_waits_for_every_other_job():
    jobs = _ci()["jobs"]
    assert set(jobs["ci-passed"]["needs"]) == set(jobs) - {"ci-passed"}


def test_the_test_groups_cover_the_whole_split():
    job = _ci()["jobs"]["test"]
    groups = job["strategy"]["matrix"]["group"]
    run = " ".join(s["run"] for s in job["steps"] if "pytest" in s.get("run", ""))
    splits = int(re.search(r"--splits (\d+)", run).group(1))
    assert groups == list(range(1, splits + 1))
    assert (REPO / ".test_durations").is_file()


def test_only_the_gate_lets_main_skip_the_suite():
    jobs = _ci()["jobs"]
    for name in ("test", "e2e", "build"):
        assert jobs[name]["needs"] == "gate", name
        assert jobs[name]["if"] == "needs.gate.outputs.skip != 'true'", name
    gate = " ".join(s.get("run", "") for s in jobs["gate"]["steps"])
    assert "refs/heads/main" in gate
    assert "scripts/ci_reuse_dev_run.sh" in gate


def _on(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data[True]


@pytest.mark.parametrize(
    "path",
    [p for p in WORKFLOWS if "workflow_run" in _on(p)],
    ids=lambda p: p.name,
)
def test_a_workflow_run_takes_only_this_repositorys_own_runs(path: Path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for name, job in data["jobs"].items():
        guard = " ".join(job["if"].split())
        assert "github.event.workflow_run.event != 'pull_request'" in guard, name
        assert (
            "github.event.workflow_run.head_repository.full_name"
            " == github.repository" in guard
        ), name


def _deploy_text() -> str:
    return (REPO / ".github/workflows/deploy.yml").read_text(encoding="utf-8")


def test_the_deploy_job_has_no_checkout():
    steps = _load(REPO / ".github/workflows/deploy.yml").jobs["deploy"].steps
    assert not [s for s in steps if s.uses]


def test_the_deploy_pins_the_host_key_and_never_trusts_first_use():
    text = _deploy_text()
    assert "ssh-keyscan" not in text.replace("(ssh-keyscan <host>)", "")
    assert 'missing="$missing OFM_DEPLOY_KNOWN_HOSTS' in text


def test_a_ci_deploy_never_rolls_back_but_a_manual_one_may():
    text = _deploy_text()
    assert "FORWARD_ONLY: ${{ github.event_name == 'workflow_run'" in text
    assert 'git merge-base --is-ancestor "$target" "$current"' in text
    assert "bash /tmp/ofm-deploy.sh '$DIR' '$SHA' '$FORWARD_ONLY'" in text
