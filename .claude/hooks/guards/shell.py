import re
import shlex
from pathlib import PurePosixPath

from pydantic import BaseModel

lazy from guards.event import HookEvent

PUNCTUATION = ";&|()"
PIPES = {"|", "|&"}
REDIRECT_TO_STREAM = re.compile(r"\d*>&\d+|&>")
HEREDOC = re.compile(
    r"(<<-?[ \t]*(['\"]?)(\w+)\2[^\n]*)\n(?:.*?\n)?[ \t]*\3[ \t]*(?=\n|$)", re.DOTALL
)
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
WRAPPERS = {"rtk", "sudo", "command", "time", "exec", "nohup"}
SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
SHELL_PROGRAMS = {"bash", "sh", "zsh", "powershell", "pwsh"}
LAUNCHER_VALUE_FLAGS = frozenset(
    {
        "-u",
        "--unset",
        "-C",
        "--chdir",
        "-p",
        "--package",
        "--from",
        "--with",
        "--python",
        "--directory",
        "--project",
    }
)
TIMEOUT_VALUE_FLAGS = frozenset({"-k", "--kill-after", "-s", "--signal"})
UV_VALUE_FLAGS = frozenset(
    {"--project", "--with", "--python", "--directory", "--package"}
)
FLAG_WRAPPERS = frozendict(
    {
        "npx": LAUNCHER_VALUE_FLAGS,
        "uvx": LAUNCHER_VALUE_FLAGS,
        "env": LAUNCHER_VALUE_FLAGS,
        "xargs": frozenset(
            {
                "-a",
                "-d",
                "-E",
                "-I",
                "-L",
                "-n",
                "-P",
                "-s",
                "--arg-file",
                "--delimiter",
            }
        ),
        "nice": frozenset({"-n", "--adjustment"}),
        "setsid": frozenset(),
        "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
        "ionice": frozenset({"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"}),
    }
)


def program_name(word: str) -> str:
    return PurePosixPath(word.replace("\\", "/")).name.lower().removesuffix(".exe")


class Statement(BaseModel):
    words: list[str]
    piped_to: list[list[str]] = []

    @property
    def program(self) -> str:
        return program_name(self.words[0]) if self.words else ""


def without_heredoc_bodies(command: str) -> str:
    return HEREDOC.sub(lambda found: found.group(1) + "\n", command)


def tokens(command: str, posix: bool) -> list[str]:
    text = REDIRECT_TO_STREAM.sub(" ", without_heredoc_bodies(command)).replace(
        "\n", " ; "
    )
    lexer = shlex.shlex(text, posix=posix, punctuation_chars=PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return [token if posix else token.strip("\"'") for token in lexer]
    except ValueError:
        return text.split()


def unwrapped(words: list[str]) -> list[str]:
    while words:
        head = program_name(words[0])
        if ENV_ASSIGNMENT.match(words[0]) or head in WRAPPERS:
            words = words[1:]
        elif head == "uv" and words[1:2] == ["run"]:
            words = _after_flags(words[2:], UV_VALUE_FLAGS)
        elif head == "uv" and words[1:3] == ["tool", "run"]:
            words = _after_flags(words[3:], LAUNCHER_VALUE_FLAGS)
        elif head in ("python", "python3", "py") and words[1:2] == ["-m"]:
            words = words[2:]
        elif head in FLAG_WRAPPERS:
            words = _after_flags(words[1:], FLAG_WRAPPERS[head])
        elif head == "timeout":
            words = _after_flags(words[1:], TIMEOUT_VALUE_FLAGS)[1:]
        else:
            return words
    return words


def _after_flags(words: list[str], value_flags: frozenset[str]) -> list[str]:
    index = 0
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in value_flags else 1
    return words[index:]


def shell_statements(event: HookEvent) -> list[Statement]:
    return statements(event.command, posix=event.tool_name != "PowerShell")


def statements(command: str, posix: bool = True) -> list[Statement]:
    flat = _flat_statements(command, posix)
    nested = [
        found.group(1) or found.group(2)
        for found in SUBSTITUTION.finditer(without_heredoc_bodies(command))
    ]
    for statement in flat:
        words = statement.words
        if statement.program in SHELL_PROGRAMS and "-c" in words[:-1]:
            nested.append(words[words.index("-c") + 1])
        elif statement.program == "eval":
            nested.append(" ".join(words[1:]))
    return flat + [
        inner for text in nested if text for inner in statements(text, posix)
    ]


def _flat_statements(command: str, posix: bool) -> list[Statement]:
    found: list[Statement] = []
    segments: list[list[str]] = [[]]
    for token in [*tokens(command, posix), ";"]:
        if token and set(token) <= set(PUNCTUATION):
            if token in PIPES:
                segments.append([])
                continue
            words = [unwrapped(segment) for segment in segments]
            if words[0]:
                found.append(
                    Statement(words=words[0], piped_to=[w for w in words[1:] if w])
                )
            segments = [[]]
        else:
            segments[-1].append(token)
    return found
