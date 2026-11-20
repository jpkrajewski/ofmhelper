import sys
import time
import traceback

from guards import GUARDS
from guards.event import STATE, HookEvent, Verdict
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.alias_generators import to_camel

AUDIT = STATE / "audit.jsonl"
ERRORS = STATE / "errors.log"
TARGET_LIMIT = 160


class AuditLine(BaseModel):
    at: str
    session: str
    agent: str
    event: str
    tool: str
    target: str
    verdicts: list[str]
    fields: list[str]


def target(event: HookEvent) -> str:
    raw = (
        event.tool_input.get("skill")
        or event.relative
        or event.command
        or event.tool_input.get("description")
        or event.prompt[:60]
    )
    return str(raw).replace("\n", " ")[:TARGET_LIMIT]


def audit(event: HookEvent, verdicts: list[Verdict]) -> None:
    line = AuditLine(
        at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        session=event.session_id[:8],
        agent=event.agent_type or event.agent_id or "main",
        event=event.hook_event_name,
        tool=event.tool_name,
        target=target(event),
        verdicts=[f"{v.guard}:{v.kind}" for v in verdicts],
        fields=sorted(event.model_extra or {}),
    )
    STATE.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as log:
        log.write(line.model_dump_json() + "\n")


class SpecificOutput(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, serialize_by_alias=True, validate_by_name=True
    )

    hook_event_name: str
    permission_decision: str | None = None
    permission_decision_reason: str | None = None
    additional_context: str | None = None


class HookAnswer(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, serialize_by_alias=True, validate_by_name=True
    )

    decision: str | None = None
    reason: str | None = None
    hook_specific_output: SpecificOutput | None = None


def reasons(verdicts: list[Verdict], kind: str) -> str:
    return "\n".join(v.reason for v in verdicts if v.kind == kind)


def response(event: HookEvent, verdicts: list[Verdict]) -> HookAnswer | None:
    name = event.hook_event_name
    if denied := reasons(verdicts, "deny"):
        return HookAnswer(
            hook_specific_output=SpecificOutput(
                hook_event_name=name,
                permission_decision="deny",
                permission_decision_reason=denied,
            )
        )
    if blocked := reasons(verdicts, "block"):
        return HookAnswer(decision="block", reason=blocked)
    if extra := reasons(verdicts, "context"):
        return HookAnswer(
            hook_specific_output=SpecificOutput(
                hook_event_name=name, additional_context=extra
            )
        )
    return None


def failed(where: str) -> None:
    trace = traceback.format_exc()
    STATE.mkdir(parents=True, exist_ok=True)
    with ERRORS.open("a", encoding="utf-8") as log:
        log.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {where}\n{trace}\n")
    sys.stderr.write(f"guard {where} failed, call allowed: {trace.splitlines()[-1]}\n")


def main() -> int:
    try:
        event = HookEvent.model_validate_json(sys.stdin.read())
    except ValidationError:
        failed("input")
        return 0
    try:
        verdicts = judged(event)
        audit(event, verdicts)
        answer = response(event, verdicts)
    except Exception:
        failed("dispatcher")
        return 0
    if answer:
        print(answer.model_dump_json(exclude_none=True))
    return 0


def judged(event: HookEvent) -> list[Verdict]:
    verdicts: list[Verdict] = []
    for guard in GUARDS:
        try:
            verdict = guard.check(event) if guard.applies(event) else None
        except Exception:
            failed(guard.name)
            continue
        if verdict:
            verdicts.append(verdict)
    return verdicts


if __name__ == "__main__":
    sys.exit(main())
