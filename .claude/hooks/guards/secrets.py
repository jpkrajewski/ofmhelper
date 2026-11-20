import subprocess

from guards.event import ROOT, Guard, HookEvent, Verdict, deny

NAME = "secrets"
LOCAL_ENV = ROOT / "deploy.local.env"
HOST_KEY = "OFM_DEPLOY_HOST"
PUBLIC_REPO = (
    "The production host from deploy.local.env is in {path}, a file git tracks: this repo is public. "
    "Read connection details from deploy.local.env (gitignored) instead."
)


def production_hosts() -> list[str]:
    if not LOCAL_ENV.is_file():
        return []
    values = [
        line.partition("=")[2].strip().strip("\"'")
        for line in LOCAL_ENV.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{HOST_KEY}=")
    ]
    return [value.rpartition("@")[2] for value in values if value]


def ignored(relative: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "-q", relative], check=False
        ).returncode
        == 0
    )


def check(event: HookEvent) -> Verdict | None:
    text = event.written_text
    if not event.relative or not any(host in text for host in production_hosts()):
        return None
    return (
        None
        if ignored(event.relative)
        else deny(NAME, PUBLIC_REPO.format(path=event.relative))
    )


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse"}),
    tools=frozenset({"Edit", "Write", "MultiEdit"}),
    check=check,
)
