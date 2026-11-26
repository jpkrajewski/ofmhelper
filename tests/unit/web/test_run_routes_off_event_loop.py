# Copyright (C) 2026 jpkrajewski

import inspect

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
