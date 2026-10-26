"""Telemetry across forks (ofmhelpers/telemetry.py). `rq worker-pool` forks
its workers from the pool parent and each worker forks a work horse per
job, while their export threads run. Prod 2026-10-26: two of ten workers
were forked while the parent's export thread held the connection pool's
lock; the copy stayed locked for good, so those workers sent nothing for two
days and every job's horse hung in its last flush until RQ's job timeout.

A real fork against a fake Logfire (`LOGFIRE_BASE_URL`), in a subprocess:
configuring Logfire for real must not touch the test process."""

import gzip
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from ofmhelpers import telemetry

# The pool parent at the fork: its export thread inside the connection
# pool's lock (urllib3 `PoolManager.pools.lock`), as py-spy showed on prod.
_FORK_DURING_AN_EXPORT = r"""
import gc, os, threading

import logfire
from logfire._internal.exporters.otlp import OTLPExporterHttpSession

from ofmhelpers import telemetry

telemetry.configure_telemetry("ascend-fork-test")

locks = [
    adapter.poolmanager.pools.lock
    for session in gc.get_objects()
    if isinstance(session, OTLPExporterHttpSession)
    for adapter in set(session.adapters.values())
]
assert locks, "no Logfire session found"
held, done = threading.Event(), threading.Event()


def export_thread():
    for lock in locks:
        lock.acquire()
    held.set()
    done.wait()
    for lock in locks:
        lock.release()


threading.Thread(target=export_thread, daemon=True).start()
held.wait()
pid = os.fork()
if pid == 0:
    logfire.info("sent from the forked child")
    telemetry.flush_telemetry()
    os._exit(0)
os.waitpid(pid, 0)
done.set()
"""


class _FakeLogfire(ThreadingHTTPServer):
    """Takes OTLP over HTTP and keeps the names of the spans it got."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.span_names: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        if self.path == "/v1/traces":
            request = ExportTraceServiceRequest.FromString(body)
            cast("_FakeLogfire", self.server).span_names += [
                span.name
                for resource in request.resource_spans
                for scope in resource.scope_spans
                for span in scope.spans
            ]
        self._answer(b"", "application/x-protobuf")

    def do_GET(self) -> None:  # the token check
        self._answer(json.dumps({"project_name": "test"}).encode(), "application/json")

    def _answer(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


@pytest.fixture
def fake_logfire() -> Iterator[_FakeLogfire]:
    server = _FakeLogfire()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_a_child_forked_during_an_export_still_sends_and_exits(fake_logfire):
    env = {
        **os.environ,
        "LOGFIRE_TOKEN": "pylf_v1_eu_test",
        "LOGFIRE_BASE_URL": fake_logfire.url,
        "OFM_TELEMETRY_FLUSH_S": "10",
    }

    subprocess.run(
        [sys.executable, "-c", _FORK_DURING_AN_EXPORT],
        env=env,
        timeout=60,
        check=True,
    )

    assert "sent from the forked child" in fake_logfire.span_names


def test_a_stuck_flush_never_holds_up_a_horse(monkeypatch):
    """The SDK's batch flush ignores its timeout: a horse waiting on it
    holds its worker until RQ's job timeout (45 min)."""
    monkeypatch.setenv("LOGFIRE_TOKEN", "pylf_v1_eu_test")
    monkeypatch.setenv("OFM_TELEMETRY_FLUSH_S", "0.2")
    stuck = threading.Event()
    monkeypatch.setattr(telemetry.logfire, "force_flush", lambda **_: stuck.wait())
    horse = threading.Thread(target=telemetry.flush_telemetry, daemon=True)

    horse.start()
    horse.join(5)
    returned = not horse.is_alive()
    stuck.set()

    assert returned
