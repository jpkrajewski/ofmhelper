import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(os.environ.get("CLAUDE_GUARDS_ROOT") or Path(__file__).resolve().parents[3])
STATE = ROOT / "reports" / "hooks"
TEST_LOCK = ROOT / "reports" / ".running"
SHIP_OPEN = STATE / "ship.open"
NOTIFICATION = "<task-notification>"
STALE_LOCK_S = 3600


class HookEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str
    hook_event_name: str
    cwd: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = Field(default_factory=dict)
    prompt: str = ""
    agent_id: str = ""
    agent_type: str = ""
    stop_hook_active: bool = False
    last_assistant_message: str = ""
    duration_ms: float = 0.0

    @property
    def command(self) -> str:
        return str(self.tool_input.get("command") or "")

    @property
    def in_background(self) -> bool:
        return bool(self.tool_input.get("run_in_background"))

    @property
    def relative(self) -> str:
        raw = self.tool_input.get("file_path") or self.tool_input.get("notebook_path")
        if not raw:
            return ""
        path = Path(str(raw))
        if not path.is_absolute():
            path = Path(self.cwd or ROOT) / path
        try:
            return path.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            return ""

    @property
    def written_text(self) -> str:
        edits = self.tool_input.get("edits") or []
        parts = [
            self.tool_input.get("new_string"),
            self.tool_input.get("content"),
            self.tool_input.get("new_source"),
            *(edit.get("new_string") for edit in edits),
        ]
        return "\n".join(str(part) for part in parts if part)

    @property
    def replaced_text(self) -> str:
        edits = self.tool_input.get("edits") or []
        parts = [
            self.tool_input.get("old_string"),
            *(edit.get("old_string") for edit in edits),
        ]
        return "\n".join(str(part) for part in parts if part)

    @property
    def is_owner_prompt(self) -> bool:
        return bool(self.prompt.strip()) and not self.prompt.lstrip().startswith(
            NOTIFICATION
        )


class Verdict(BaseModel):
    guard: str
    kind: Literal["deny", "block", "context"]
    reason: str


class Guard(BaseModel):
    name: str
    events: frozenset[str]
    tools: frozenset[str] = frozenset()
    check: Callable[[HookEvent], Verdict | None]

    def applies(self, event: HookEvent) -> bool:
        return event.hook_event_name in self.events and (
            not self.tools or not event.tool_name or event.tool_name in self.tools
        )


class Markers(BaseModel):
    folder: Path

    @classmethod
    def of(cls, event: HookEvent) -> Markers:
        return cls(
            folder=STATE / "state" / f"{event.session_id}-{event.agent_id or 'main'}"
        )

    def set(self, name: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / name).write_text(str(time.time()), encoding="utf-8")

    def clear(self, name: str) -> None:
        (self.folder / name).unlink(missing_ok=True)

    def has(self, name: str) -> bool:
        return (self.folder / name).is_file()

    def since(self, name: str) -> float:
        path = self.folder / name
        return path.stat().st_mtime if path.is_file() else 0.0


def owner_said_ship(event: HookEvent) -> bool:
    return Markers.of(event).has("owner-ship") or SHIP_OPEN.is_file()


def test_run_since() -> str:
    if not TEST_LOCK.is_file():
        return ""
    started = TEST_LOCK.stat().st_mtime
    if time.time() - started > STALE_LOCK_S:
        return ""
    return time.strftime("%H:%M:%S", time.localtime(started))


def deny(guard: str, reason: str) -> Verdict:
    return Verdict(guard=guard, kind="deny", reason=reason)


def block(guard: str, reason: str) -> Verdict:
    return Verdict(guard=guard, kind="block", reason=reason)


def context(guard: str, text: str) -> Verdict:
    return Verdict(guard=guard, kind="context", reason=text)
