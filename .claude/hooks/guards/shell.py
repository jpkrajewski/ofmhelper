# Copyright (C) 2026 jpkrajewski

import base64
import re
import shlex
from pathlib import PurePosixPath

from pydantic import BaseModel

lazy from guards.event import HookEvent

PUNCTUATION = ";&|()"
PIPES = {"|", "|&"}
REDIRECT_TO_STREAM = re.compile(r"\d*>&\d+|&>")
HEREDOC = re.compile(
    r"(<<-?[ \t]*(['\"]?)(\w+)\2[^\n]*)\n((?:.*?\n)?)[ \t]*\3[ \t]*(?=\n|$)",
    re.DOTALL,
)
QUOTE_PAIR = re.compile(r"([\"'])(.*?)\1")
ANSI_QUOTED = re.compile(r"(?<![\w'\"\\])\$'((?:[^'\\]|\\.)*)'")
LOCALE_QUOTED = re.compile(r"\$(?=\")")
LINE_CONTINUATION = re.compile(r"\\\r?\n")
POWERSHELL_CONTINUATION = re.compile(r"`\r?\n")
POWERSHELL_ESCAPE = re.compile(r"`(?=\S)")
MAX_NESTING = 3
UNREADABLE = "unreadable-command"
ENCODED_FLAG = "encodedcommand"
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
WRAPPERS = {"rtk", "sudo", "command", "time", "exec", "nohup"}
SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
SHELL_PROGRAMS = {"bash", "sh", "zsh", "dash", "ksh", "powershell", "pwsh", "cmd"}
JOINING_SHELLS = {"powershell", "pwsh", "cmd"}
EVAL_PROGRAMS = {"eval", "iex", "invoke-expression"}
INLINE_FLAG = re.compile(
    r"^-(?:[a-z]*c|com(?:m(?:a(?:n(?:d)?)?)?)?)$|^/[ck]$",
    re.IGNORECASE,
)
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
    },
)
TIMEOUT_VALUE_FLAGS = frozenset({"-k", "--kill-after", "-s", "--signal"})
UV_VALUE_FLAGS = frozenset(
    {"--project", "--with", "--python", "--directory", "--package"},
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
            },
        ),
        "nice": frozenset({"-n", "--adjustment"}),
        "setsid": frozenset(),
        "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
        "ionice": frozenset({"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"}),
    },
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


def decoded_ansi_quotes(text: str) -> str:
    def decode(found: re.Match[str]) -> str:
        try:
            return shlex.quote(found.group(1).encode().decode("unicode_escape"))
        except UnicodeError:  # silent: an undecodable $'..' stays as typed
            return found.group(0)

    return LOCALE_QUOTED.sub("", ANSI_QUOTED.sub(decode, text))


def without_inner_quotes(token: str) -> str:
    plain = LOCALE_QUOTED.sub("", token.replace("$'", "'"))
    paired = QUOTE_PAIR.sub(r"\2", plain)
    return paired if paired != plain else plain.strip("\"'")


def tokens(command: str, posix: bool) -> list[str]:  # noqa: FBT001
    joined = LINE_CONTINUATION.sub(" ", without_heredoc_bodies(command))
    if not posix:
        joined = POWERSHELL_CONTINUATION.sub(" ", joined)
    text = REDIRECT_TO_STREAM.sub(" ", joined).replace("\n", " ; ")
    plain = decoded_ansi_quotes(text) if posix else POWERSHELL_ESCAPE.sub("", text)
    return lexed(plain, posix)


def lexed(text: str, posix: bool) -> list[str]:  # noqa: FBT001
    lexer = shlex.shlex(text, posix=posix, punctuation_chars=PUNCTUATION)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return [token if posix else without_inner_quotes(token) for token in lexer]
    except ValueError:  # silent: an unparseable command is refused as UNREADABLE
        return [UNREADABLE]


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


def is_encoded_flag(word: str) -> bool:
    name = word.lower().removeprefix("-")
    return word.startswith("-") and (
        name in ("e", "ec") or (name.startswith("en") and ENCODED_FLAG.startswith(name))
    )


def decoded_command(encoded: str) -> str:
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-16-le")
    except (
        ValueError
    ):  # silent: an unreadable command is returned as UNREADABLE and refused
        return UNREADABLE


def inline_script(words: list[str]) -> str:
    program = program_name(words[0]) if words else ""
    if program in EVAL_PROGRAMS:
        return " ".join(words[1:])
    if program not in SHELL_PROGRAMS:
        return ""
    for index, word in enumerate(words[1:], 1):
        if program in ("powershell", "pwsh") and is_encoded_flag(word):
            rest = words[index + 1 :]
            return decoded_command(rest[0]) if rest else UNREADABLE
        if INLINE_FLAG.match(word):
            rest = words[index + 1 :]
            if program in JOINING_SHELLS:
                return " ".join(rest)
            return rest[0] if rest else ""
    return ""


def shell_statements(event: HookEvent) -> list[Statement]:
    return statements(event.command, posix=event.tool_name != "PowerShell")


def heredoc_scripts(command: str, posix: bool) -> list[str]:  # noqa: FBT001
    scripts = []
    for found in HEREDOC.finditer(command):
        line_start = command.rfind("\n", 0, found.start()) + 1
        lead = _flat_statements(command[line_start : found.end(1)], posix)
        programs = {
            program_name(words[0])
            for statement in lead
            for words in (statement.words, *statement.piped_to)
            if words
        }
        if programs & SHELL_PROGRAMS:
            scripts.append(found.group(4))
    return scripts


def statements(command: str, posix: bool = True, depth: int = 0) -> list[Statement]:  # noqa: FBT001, FBT002
    flat = _flat_statements(command, posix)
    nested = [
        found.group(1) or found.group(2)
        for found in SUBSTITUTION.finditer(without_heredoc_bodies(command))
    ]
    nested += [
        inline_script(words)
        for statement in flat
        for words in (statement.words, *statement.piped_to)
    ]
    nested += heredoc_scripts(command, posix)
    texts = list(dict.fromkeys(text for text in nested if text.strip()))
    if depth >= MAX_NESTING:
        return flat + ([Statement(words=[UNREADABLE])] if texts else [])
    return flat + [
        inner for text in texts for inner in statements(text, posix, depth + 1)
    ]


def _flat_statements(command: str, posix: bool) -> list[Statement]:  # noqa: FBT001
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
                    Statement(words=words[0], piped_to=[w for w in words[1:] if w]),
                )
            segments = [[]]
        else:
            segments[-1].append(token)
    return found
