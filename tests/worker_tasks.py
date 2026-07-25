"""
Importable task functions for the RQ integration test. RQ runs a job by
importing its function by module path, so these must live at real module level
(not nested inside a test function) to be resolvable by the worker. Named
without a `test_` prefix so pytest doesn't try to collect it as a test module.
"""


def make_result(name):
    """Mimics a generator task fn: returns the one-file-per-entry result shape."""
    return [{"name": name, "path": None}]


def always_fails():
    """Mimics a task that raises (e.g. a bad API key)."""
    raise ValueError("Wrong API Key")
