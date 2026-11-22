import ast
import io
import re
import sys
import tokenize
from pathlib import Path

DIRECTIVES = ("# noqa", "# type: ignore", "# fmt:", "# pragma", "# silent:")
PYTHON_ROOTS = ("src/", "tests/", "alembic/")
WEB_STATIC = "src/ofmhelpers/web/static/"
WEB_TEMPLATES = "src/ofmhelpers/web/templates/"
SKIPPED = ("tests/fixtures/",)
REGEX_MAY_FOLLOW = set("(,=:[!&|?{};+-*%<>~^")
REGEX_MAY_FOLLOW_WORDS = (
    "return",
    "typeof",
    "case",
    "do",
    "else",
    "in",
    "of",
    "void",
    "yield",
    "await",
)
TEMPLATE_COMMENT = re.compile(r"\{#(?!\s*djlint:)|<!--")


def python_comment_lines(source: str) -> list[int]:
    return [
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT and not token.string.startswith(DIRECTIVES)
    ]


def python_docstring_lines(source: str) -> list[int]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return [
        1 if isinstance(node, ast.Module) else node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, owners) and ast.get_docstring(node) is not None
    ]


def regex_may_start(code_before: str) -> bool:
    before = code_before.rstrip()
    word = re.search(r"([A-Za-z_$]+)$", before)
    return (
        not before
        or before[-1] in REGEX_MAY_FOLLOW
        or bool(word and word.group(1) in REGEX_MAY_FOLLOW_WORDS)
    )


def end_of_regex(text: str, start: int) -> int:
    position, in_class = start + 1, False
    while position < len(text) and text[position] != "\n":
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            return position + 1
        position += 1
    return position


def end_of_string(text: str, start: int) -> int:
    quote, position = text[start], start + 1
    while position < len(text) and text[position] != quote:
        position += 2 if text[position] == "\\" else 1
    return position + 1


def script_comment_lines(text: str, *, css: bool) -> list[int]:
    found, position = [], 0
    while position < len(text):
        char, following = text[position], text[position + 1 : position + 2]
        if char in "'\"" or (char == "`" and not css):
            position = end_of_string(text, position)
        elif char == "/" and following in ("*", "/") and not (css and following == "/"):
            found.append(text.count("\n", 0, position) + 1)
            closer = "*/" if following == "*" else "\n"
            end = text.find(closer, position + 2)
            position = len(text) if end < 0 else end + len(closer)
        elif char == "/" and not css and regex_may_start(text[:position]):
            position = end_of_regex(text, position)
        else:
            position += 1
    return found


def template_comment_lines(text: str) -> list[int]:
    return [
        text.count("\n", 0, match.start()) + 1
        for match in TEMPLATE_COMMENT.finditer(text)
    ]


def problem_lines(relative: str, text: str) -> list[int]:
    if relative.startswith(SKIPPED):
        return []
    if relative.endswith(".py") and relative.startswith(PYTHON_ROOTS):
        return sorted(python_comment_lines(text) + python_docstring_lines(text))
    if relative.startswith(WEB_STATIC) and relative.endswith((".js", ".css")):
        return script_comment_lines(text, css=relative.endswith(".css"))
    if relative.startswith(WEB_TEMPLATES) and relative.endswith(".html"):
        return template_comment_lines(text)
    return []


def every_file(root: Path) -> list[str]:
    return [
        path.relative_to(root).as_posix()
        for top in ("src", "tests", "alembic")
        for path in sorted((root / top).rglob("*"))
        if path.suffix in (".py", ".js", ".css", ".html")
        and "__pycache__" not in path.parts
    ]


def problems(root: Path, relatives: list[str]) -> list[str]:
    found = []
    for relative in relatives:
        path = root / relative
        if path.suffix not in (".py", ".js", ".css", ".html") or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        found += [
            f"{relative}:{line}"
            for line in problem_lines(relative.replace("\\", "/"), text)
        ]
    return found


def main(arguments: list[str]) -> int:
    root = Path.cwd()
    found = problems(root, arguments)
    for spot in found:
        print(f"{spot}: comment or docstring (CLAUDE.md: names say it)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
