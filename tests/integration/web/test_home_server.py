"""/home's Server section (features/server): admins only, fetched from
/home/server; the host numbers read from /proc and the disk; what is worth
a warning."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from signin import sign_in, sign_in_admin

from ofmhelpers.cache.queue import QueueLoad
from ofmhelpers.features.server import host
from ofmhelpers.features.server.models import Host, RunningJob, Usage
from ofmhelpers.features.server.status import duration, problems
from ofmhelpers.web.main import app

GB = 1024**3


def test_an_admin_gets_the_section_and_its_body():
    client = TestClient(app)
    sign_in_admin(client)

    assert 'id="server"' in client.get("/home").text
    body = client.get("/home/server")
    assert body.status_code == 200
    for part in ("CPU", "RAM", "Disk", "Database", "Queues", "Running jobs", "Uploads"):
        assert part in body.text


def test_a_user_gets_neither():
    client = TestClient(app)
    sign_in(client)

    assert 'id="server"' not in client.get("/home").text
    assert client.get("/home/server").status_code == 403


def _host(cpu=10.0, load=0.5, ram=0.3, disk=0.5) -> Host:
    return Host(
        cpus=4,
        cpu_percent=cpu,
        load=(load, load, load),
        memory=Usage(used=int(ram * 8 * GB), total=8 * GB),
        swap=Usage(used=0, total=0),
        disk=Usage(used=int(disk * 72 * GB), total=72 * GB),
        uptime_s=3600,
    )


def _queue(name="media", workers=1, waiting=0, wait=None) -> QueueLoad:
    return QueueLoad(
        name=name,
        workers=workers,
        busy=0,
        waiting=waiting,
        failed=0,
        oldest_wait_s=wait,
    )


def test_a_quiet_box_has_no_problem():
    assert (
        problems(_host(), [_queue()], [RunningJob(task="clone", who="a", running_s=60)])
        == []
    )


def test_what_is_high_is_worded():
    found = problems(
        _host(cpu=97, load=6.0, ram=0.9, disk=0.91),
        [_queue("default", workers=0, waiting=2), _queue("media", waiting=3, wait=900)],
        [RunningJob(task="trials", who="a", running_s=7200)],
    )

    assert found == [
        "CPU 97% busy",
        "Load 6.0 on 4 CPUs: work is waiting for CPU",
        "RAM 90% used",
        "Disk 91% full",
        "Queue default: 2 waiting, no worker running",
        "Queue media: 3 waiting, first one for 15 min",
        "1 job running over 1.0 h",
    ]


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(12, "12 s"), (300, "5 min"), (9000, "2.5 h"), (259200, "3 d")],
)
def test_durations_read_short(seconds, text):
    assert duration(seconds) == text


def test_the_host_numbers_come_from_proc(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text("cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 1 1 1\n")
    (proc / "meminfo").write_text(
        "MemTotal:       8000000 kB\nMemFree:  100 kB\nMemAvailable:   2000000 kB\n"
        "SwapTotal:      1000000 kB\nSwapFree:       1000000 kB\n"
    )
    (proc / "loadavg").write_text("1.50 1.00 0.50 2/300 4242\n")
    (proc / "uptime").write_text("7200.55 1000.00\n")
    monkeypatch.setattr(host, "PROC", proc)

    box = host.host(tmp_path, 0.01)

    assert box.memory.used == 6000000 * 1024
    assert box.memory.percent == pytest.approx(75)
    assert box.swap.used == 0
    assert box.load == (1.5, 1.0, 0.5)
    assert box.uptime_s == pytest.approx(7200.55)
    assert box.cpu_percent == 0  # the same ticks both reads
    assert box.disk.total > 0


def test_the_uploads_folders_biggest_first_empty_ones_left_out(tmp_path: Path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.mp4").write_bytes(b"0" * 300)
    (tmp_path / "assets" / "deep").mkdir()
    (tmp_path / "assets" / "deep" / "b.png").write_bytes(b"0" * 100)
    (tmp_path / "trials").mkdir()
    (tmp_path / "trials" / "c.mp4").write_bytes(b"0" * 50)
    (tmp_path / "empty").mkdir()
    (tmp_path / "loose.txt").write_bytes(b"0" * 10)

    found = [(f.name, f.bytes, f.files) for f in host.folders(tmp_path)]

    assert found == [("assets", 400, 2), ("trials", 50, 1), ("(files)", 10, 1)]
