# Copyright (C) 2026 jpkrajewski

import importlib.util
import sys
lazy from collections.abc import Callable
lazy from pathlib import Path

import pytest
import requests
from pydantic import BaseModel
from repo_paths import REPO

sys.path.insert(0, str(REPO / "scripts"))
_spec = importlib.util.spec_from_file_location("ui_check", REPO / "scripts/ui_check.py")
assert _spec is not None
assert _spec.loader is not None
ui_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ui_check)

POST = "**/clone/run"
APP = "http://ofmhelpers:8000"


class FakeSocket(BaseModel):
    url: str


class FakePage:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable] = {}
        self.socket_routes: list[tuple[str, Callable]] = []

    def on(self, event: str, handler: Callable) -> None:
        self.handlers[event] = handler

    def route_web_socket(self, url: str, handler: Callable) -> None:
        self.socket_routes.append((url, handler))


class FakeContext:
    def __init__(self) -> None:
        self.page = FakePage()

    def new_page(self) -> FakePage:
        return self.page


def _last_line(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("OFM_UI_PASSWORD", "not-the-real-one")
    monkeypatch.setattr(ui_check, "OUT", tmp_path)
    monkeypatch.setattr(ui_check, "app_down", lambda _base_url: None)
    return tmp_path


def _checked_with(steps: list, made: list[Path]) -> Callable:
    def checked(_args, _form, _base_url, out: Path) -> list:
        out.mkdir(parents=True)
        made.append(out)
        return steps

    return checked


def _raises(error: Exception) -> Callable:
    def checked(_args, _form, _base_url, _out: Path) -> list:
        raise error

    return checked


def test_the_mocked_steps_fake_the_notifications_socket_and_fail_on_one_that_reached_the_server():  # noqa: E501
    context = FakeContext()
    watch = ui_check.Watch(context)
    [(glob, fake)] = context.page.socket_routes
    assert glob == "**/notifications/ws"
    fake(FakeSocket(url="ws://ofmhelpers:8000/notifications/ws"))
    assert watch.problems(ui_check.never) == []
    assert watch.lines == [
        "[socket] faked ws://ofmhelpers:8000/notifications/ws, never connected",
    ]
    context.page.handlers["websocket"](FakeSocket(url="ws://ofmhelpers:8000/other"))
    assert watch.problems(ui_check.never) == [
        "a WebSocket reached the server: ws://ofmhelpers:8000/other",
    ]


def test_the_real_step_leaves_the_sockets_alone():
    context = FakeContext()
    ui_check.Watch(context, fake_sockets=False)
    assert context.page.socket_routes == []
    assert "websocket" not in context.page.handlers


@pytest.mark.parametrize("path", ["/clone", "/clone-long", "/generate", "/edit"])
def test_real_is_refused_on_every_paid_page(
    signed: Path,
    capsys: pytest.CaptureFixture[str],
    path: str,
):
    assert ui_check.run([path, "--real"]) == 2
    assert _last_line(capsys) == (
        f"ui_check: FAIL --real only on /download-assets, /spoof: {path}"
        " pays a provider, mock only"
    )
    assert list(signed.iterdir()) == []


@pytest.mark.parametrize("path", ["/download-assets", "/spoof"])
def test_real_is_allowed_on_the_free_pages(signed: Path, path: str):
    args = ui_check.Args(path=path, real=True)
    assert ui_check.refusal(args, ui_check.form_for(args)) is None


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "url",
    [
        f"{APP}/clone-long/run",
        f"{APP}/clone/run?again=1",
        f"{APP}/download-media/run",
        f"{APP}/login",
        "https://api.kie.ai/api/v1/jobs/createTask",
    ],
)
def test_a_write_outside_the_post_glob_is_aborted(method: str, url: str):
    assert ui_check.verdict(method, url, POST) == "abort"


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_only_a_post_to_the_post_glob_is_answered(method: str):
    assert ui_check.verdict("POST", f"{APP}/clone/run", POST) == "answer"
    assert ui_check.verdict(method, f"{APP}/clone/run", POST) == "abort"


def test_reads_go_through_and_the_fake_job_status_is_answered():
    assert ui_check.verdict("GET", f"{APP}/clone", POST) == "pass"
    assert ui_check.verdict("GET", f"{APP}/clone/run", POST) == "pass"
    assert ui_check.verdict("HEAD", f"{APP}/static/js/clone.js", POST) == "pass"
    status = f"{APP}/clone/jobs/{ui_check.FAKE_JOB}/status"
    assert ui_check.verdict("GET", status, POST) == "running"


def test_a_passing_run_exits_0_and_ends_on_pass_with_the_report(
    signed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    made: list[Path] = []
    steps = [ui_check.Step(name="load", done="loaded")]
    monkeypatch.setattr(ui_check, "checked", _checked_with(steps, made))
    monkeypatch.setattr(sys, "argv", ["ui_check.py", "/clone"])
    assert ui_check.main() == 0
    [out] = made
    assert _last_line(capsys) == f"ui_check: PASS {out / 'report.md'}"
    assert "| load | pass | - | - | loaded |" in (out / "report.md").read_text()


def test_a_failing_step_exits_1_and_ends_on_fail_with_the_report(
    signed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    made: list[Path] = []
    steps = [
        ui_check.Step(name="load", done="loaded"),
        ui_check.Step(name="answer 500", problems=["no message shown"]),
    ]
    monkeypatch.setattr(ui_check, "checked", _checked_with(steps, made))
    assert ui_check.run(["/clone"]) == 1
    [out] = made
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-2] == "ui_check: [fail] answer 500: no message shown"
    assert lines[-1] == f"ui_check: FAIL {out / 'report.md'}"


def test_a_dev_app_that_does_not_answer_exits_2_and_ends_on_down(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.setenv("OFM_UI_PASSWORD", "not-the-real-one")
    monkeypatch.setenv("OFM_UI_BASE_URL", "http://nowhere:1")

    def refused(*_args: object, **_kwargs: object) -> None:
        raise requests.ConnectionError

    monkeypatch.setattr(ui_check.requests, "get", refused)
    monkeypatch.setattr(ui_check, "checked", _raises(AssertionError("never run")))
    assert ui_check.run(["/clone"]) == 2
    assert _last_line(capsys).startswith(
        "ui_check: DOWN the dev app at http://nowhere:1 does not answer"
        " (ConnectionError); start it:",
    )


def test_a_sign_in_that_cannot_reach_the_app_ends_on_down(
    signed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    error = ui_check.DevUnreachableError("could not sign in at http://x: timeout")
    monkeypatch.setattr(ui_check, "checked", _raises(error))
    assert ui_check.run(["/clone"]) == 2
    assert _last_line(capsys) == "ui_check: DOWN could not sign in at http://x: timeout"


def test_a_wrong_password_ends_on_fail(
    signed: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    error = ui_check.SignInError("sign-in failed")
    monkeypatch.setattr(ui_check, "checked", _raises(error))
    assert ui_check.run(["/clone"]) == 2
    assert _last_line(capsys) == "ui_check: FAIL sign-in failed"


def test_bad_arguments_and_a_missing_password_end_on_fail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.delenv("OFM_UI_PASSWORD", raising=False)
    assert ui_check.run([]) == 2
    assert _last_line(capsys) == (
        "ui_check: FAIL usage: ui_check.py <path> [--submit <sel> --post <glob>]"
        " [--real]"
    )
    assert ui_check.run(["/clone"]) == 2
    assert _last_line(capsys) == (
        "ui_check: FAIL set OFM_UI_PASSWORD (APP_PASSWORD_ADMIN of .env)"
    )


def test_a_crash_exits_1_and_still_ends_on_fail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    def crashed(_argv: list[str]) -> int:
        msg = "boom\nsecond line"
        raise RuntimeError(msg)

    monkeypatch.setattr(ui_check, "run", crashed)
    assert ui_check.main() == 1
    captured = capsys.readouterr()
    assert captured.out.strip().splitlines()[-1] == (
        "ui_check: FAIL the check crashed: RuntimeError: boom"
    )
    assert "Traceback" in captured.err
