import re

from pydantic import BaseModel

from guards.event import ROOT, Guard, HookEvent, Markers, Verdict, deny

NAME = "skills"
EDITS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
LOAD_FIRST = (
    "CLAUDE.md STANDARDS: load the {skills} skill(s) with the Skill tool before editing {path}, "
    "then say in two lines where the new thing goes and which existing file it follows."
)


class SkillRule(BaseModel):
    path: re.Pattern[str]
    skills: tuple[str, ...]
    new_files_only: bool = False
    new_code_only: bool = False

    def needed(self, relative: str, is_new: bool, adds_names: bool) -> bool:
        if not self.path.search(relative):
            return False
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
            r"^src/ofmhelpers/(web/routers|web/static/js|features|integrations)/.*\.(py|js|html)$"
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
            r"^(docker-compose[^/]*\.ya?ml|Dockerfile[^/]*|\.github/workflows/.+|scripts/(deploy|[^/]*backup)[^/]*)$"
        ),
        skills=("aws-devops-guidance",),
    ),
)


def skill_marker(name: str) -> str:
    return f"skill-{name.rsplit(':', maxsplit=1)[-1]}"


def required(relative: str, is_new: bool, adds_names: bool) -> list[str]:
    return [
        skill
        for rule in RULES
        if rule.needed(relative, is_new, adds_names)
        for skill in rule.skills
    ]


def loaded(event: HookEvent, skill: str) -> bool:
    marker = skill_marker(skill)
    return Markers.of(event).has(marker) or Markers.main(event).has(marker)


def check(event: HookEvent) -> Verdict | None:
    if event.hook_event_name == "PostToolUse":
        skill = str(event.tool_input.get("skill") or "")
        if skill:
            Markers.of(event).set(skill_marker(skill))
        return None
    if not event.relative:
        return None
    is_new = not (ROOT / event.relative).exists()
    needed = required(event.relative, is_new, bool(event.added_names()))
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
