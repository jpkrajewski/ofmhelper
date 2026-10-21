"""Every upload `/run` route is a plain `def`: they hash and copy uploaded files
with blocking I/O, and inside an `async def` that ran on uvicorn's one event
loop -- one big upload froze every other request (galleries, polling, pages).
A plain `def` runs in FastAPI's threadpool instead."""

import inspect
import os

os.environ.setdefault("SESSION_SECRET", "test-secret")

from fastapi.routing import APIRoute

from ofmhelpers.web.routers import ROUTERS


def test_run_routes_do_not_block_the_event_loop():
    runs = [
        r
        for router in ROUTERS
        for r in router.routes
        if isinstance(r, APIRoute) and r.path.endswith("/run")
    ]
    assert {"/ai/run", "/clone/run"} <= {r.path for r in runs}
    blocking = [r.path for r in runs if inspect.iscoroutinefunction(r.endpoint)]
    assert blocking == []
