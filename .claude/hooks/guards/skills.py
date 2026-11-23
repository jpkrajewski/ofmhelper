# Copyright (C) 2026 jpkrajewski

import re

from pydantic import BaseModel

from guards.event import ROOT, Guard, HookEvent, Markers, Verdict, deny

NAME = "skills"
EDITS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
IMPORT = re.compile(
    r"^[ \t]*(?:lazy[ \t]+)?(?:from[ \t]+\S+[ \t]+)?import[ \t]+(?:\([^)]*\)|[^\n;]*)",
    re.MULTILINE,
)
PYDANTIC = re.compile(
    r"\b(?:BaseModel|BaseSettings|RootModel|TypeAdapter|ConfigDict|model_validate(?:_json)?"
    r"|model_dump(?:_json)?|model_copy|model_construct|model_fields(?:_set)?|field_validator"
    r"|model_validator|from_attributes)\b|\bField\(",
)
LOAD_FIRST = (
    "CLAUDE.md STANDARDS: load the {skills} skill(s) "
    "with the Skill tool before editing {path}, "
    "then say in two lines where the new thing goes and which existing file it follows."
)


class SkillRule(BaseModel):
    path: re.Pattern[str]
    skills: tuple[str, ...]
    new_files_only: bool = False
    new_code_only: bool = False
    writes: re.Pattern[str] | None = None

    def needed(
        self,
        relative: str,
        is_new: bool,  # noqa: FBT001
        adds_names: bool,  # noqa: FBT001
        written: str,
    ) -> bool:
        if not self.path.search(relative):
            return False
        if self.writes is not None:
            return bool(self.writes.search(written))
        if self.new_files_only:
            return is_new
        return is_new or adds_names or not self.new_code_only


RULES = (
    SkillRule(
        path=re.compile(r"^(src|tests|scripts|\.claude/hooks)/.*\.py$"),
        skills=("senior-python-rules",),
    ),
    SkillRule(
        path=re.compile(r"^src/ofmhelpers/web/(routers/|main\.py$)"),
        skills=("fastapi", "rest-api"),
    ),
    SkillRule(
        path=re.compile(r"^src/ofmhelpers/(features|integrations)/"),
        skills=("architecture",),
        new_files_only=True,
    ),
    SkillRule(
        path=re.compile(
            r"^src/ofmhelpers/(web/routers|web/static/js|features|integrations)/.*\.(py|js|html)$",
        ),
        skills=("failure-modes",),
        new_code_only=True,
    ),
    SkillRule(
        path=re.compile(r"(^|/)(jobs|tasks|worker|sweeps|queues?)(\.py$|/)"),
        skills=("rq",),
    ),
    SkillRule(
        path=re.compile(
            r"^(docker-compose[^/]*\.ya?ml|Dockerfile[^/]*|\.github/workflows/.+|scripts/(deploy|[^/]*backup)[^/]*)$",
        ),
        skills=("aws-devops-guidance",),
    ),
    SkillRule(
        path=re.compile(
            r"^(src|tests|scripts|alembic|\.claude/(hooks|skills))/.*\.py$",
        ),
        skills=("pydantic",),
        writes=PYDANTIC,
    ),
)


def skill_marker(name: str) -> str:
    return f"skill-{name.rsplit(':', maxsplit=1)[-1]}"


def required(
    relative: str,
    is_new: bool,  # noqa: FBT001
    adds_names: bool,  # noqa: FBT001
    written: str = "",
) -> list[str]:
    return [
        skill
        for rule in RULES
        if rule.needed(relative, is_new, adds_names, written)
        for skill in rule.skills
    ]


def loaded(event: HookEvent, skill: str) -> bool:
    marker = skill_marker(skill)
    return Markers.of(event).has(marker) or Markers.main(event).has(marker)


def without_imports(text: str) -> list[str]:
    return [line.strip() for line in IMPORT.sub("", text).splitlines() if line.strip()]


def only_imports_change(event: HookEvent) -> bool:
    if event.tool_name not in {"Edit", "MultiEdit"}:
        return False
    return without_imports(event.replaced_text) == without_imports(event.written_text)


def check(event: HookEvent) -> Verdict | None:
    if event.hook_event_name == "PostToolUse":
        skill = str(event.tool_input.get("skill") or "")
        if skill:
            Markers.of(event).set(skill_marker(skill))
        return None
    if not event.relative or only_imports_change(event):
        return None
    target = ROOT / event.relative
    is_new = not target.exists()
    around = "" if is_new else target.read_text(encoding="utf-8", errors="replace")
    needed = required(
        event.relative,
        is_new,
        bool(event.added_names()),
        f"{event.written_text}\n{around}",
    )
    missing = [s for s in needed if not loaded(event, s)]
    if not missing:
        return None
    return deny(NAME, LOAD_FIRST.format(skills=", ".join(missing), path=event.relative))


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse", "PostToolUse"}),
    tools=frozenset({*EDITS, "Skill"}),
    check=check,
)
