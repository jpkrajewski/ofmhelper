"""GitHub Actions workflows stay off deprecated runtimes and moving runners.

GitHub warned on every job (2026-10): actions built for Node 20 are forced
onto Node 24, and `ubuntu-latest` moves to Ubuntu 26 from 2026-10-19. Each
action here is pinned to a major whose `action.yml` declares `node24`
(checked against the action's own repository), and every job names its
runner image.
"""

import re
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field
from repo_paths import REPO

WORKFLOWS = sorted((REPO / ".github/workflows").glob("*.yml"))

# The first major of each action that runs on Node 24.
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
    # Only `jobs`: YAML 1.1 reads the `on:` key as the boolean True.
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Workflow.model_validate({"jobs": data["jobs"]})


def test_there_are_workflows():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "deploy.yml", "promote.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_names_its_runner_image(path: Path):
    for name, job in _load(path).jobs.items():
        assert job.runs_on == RUNNER, f"{path.name}:{name} runs on {job.runs_on}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_on_a_node24_major(path: Path):
    for name, job in _load(path).jobs.items():
        for step in job.steps:
            if step.uses is None:
                continue
            action, _, ref = step.uses.partition("@")
            assert action in NODE24_MAJOR, f"{path.name}:{name}: vet {action} first"
            major = re.fullmatch(r"v(\d+)(\.\d+){0,2}", ref)
            assert major, f"{path.name}:{name}: {step.uses} is not a version tag"
            assert int(major.group(1)) >= NODE24_MAJOR[action], step.uses
