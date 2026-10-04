"""The `except`-block logging contract: the traceback goes in `exc_info`, never
into the message. Interpolating the exception loses the stack that says *where*
it came from, which is the only reason these paths log at all -- they all
swallow the error and return a degraded result."""

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "ofmhelpers.scraping.apify",
        "ofmhelpers.scraping.lead_hunt",
        "ofmhelpers.scraping.aggregators",
    ],
)
def test_no_module_logs_a_bare_exception_variable(module_name):
    """Guards the whole rule rather than one call site: `, exc)` as the last
    argument of a logger call is the shape this phase removed."""
    import importlib
    import inspect
    import re

    source = inspect.getsource(importlib.import_module(module_name))
    assert not re.search(r"\n\s+exc,\n", source), (
        f"{module_name} passes an exception as a logger argument"
    )
