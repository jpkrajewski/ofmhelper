# noqa: CPY001

import importlib.util
import json
from pathlib import Path

import pytest

script_path = Path(__file__).parents[3] / "scripts" / "quality_metrics.py"
spec = importlib.util.spec_from_file_location("quality_metrics", script_path)
assert spec is not None
assert spec.loader is not None
quality_metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality_metrics)


def analyze_source(tmp_path, source, blocks):
    root = tmp_path / "project"
    source_path = root / "src" / "ofmhelpers" / "probe.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source, encoding="utf-8")
    radon_path = tmp_path / "radon-cc.json"
    radon_path.write_text(
        json.dumps({"src/ofmhelpers/probe.py": blocks}),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    assert quality_metrics.main([str(root), str(radon_path), str(output)]) == 0
    return json.loads((output / "custom-metrics.json").read_text(encoding="utf-8"))


def callable_named(metrics, name):
    return next(record for record in metrics["callables"] if record["name"] == name)


@pytest.mark.parametrize(
    "source",
    [
        "def probe():\n    if True:\n        pass\n",
        "def probe():\n    for value in ():\n        pass\n",
        "async def probe():\n    async for value in ():\n        pass\n",
        "def probe():\n    while True:\n        pass\n",
        "def probe():\n    try:\n        pass\n    except ValueError:\n        pass\n",
        "def probe():\n    try:\n        pass\n    except* ValueError:\n        pass\n",
        "def probe():\n    with open() as stream:\n        pass\n",
        "async def probe():\n    async with open() as stream:\n        pass\n",
        "def probe():\n    match 1:\n        case 1:\n            pass\n",
    ],
)
def test_each_control_flow_node_adds_one_nesting_level(tmp_path, source):
    line_count = len(source.splitlines())
    blocks = [
        {
            "type": "function",
            "name": "probe",
            "lineno": 1,
            "endline": line_count,
            "complexity": 1,
        },
    ]

    metrics = analyze_source(tmp_path, source, blocks)

    assert callable_named(metrics, "probe")["max_nesting_depth"] == 1


def test_no_control_flow_has_zero_nesting(tmp_path):
    blocks = [
        {
            "type": "function",
            "name": "probe",
            "lineno": 1,
            "endline": 2,
            "complexity": 1,
        },
    ]

    metrics = analyze_source(tmp_path, "def probe():\n    return 1\n", blocks)

    assert callable_named(metrics, "probe")["max_nesting_depth"] == 0
    assert callable_named(metrics, "probe")["kind"] == "function"


def test_elif_does_not_add_to_the_original_if_depth(tmp_path):
    source = (
        "def probe(value):\n"
        "    if value == 1:\n"
        "        if value:\n"
        "            pass\n"
        "    elif value == 2:\n"
        "        pass\n"
        "    else:\n"
        "        pass\n"
    )
    blocks = [
        {
            "type": "function",
            "name": "probe",
            "lineno": 1,
            "endline": 7,
            "complexity": 3,
        },
    ]

    metrics = analyze_source(tmp_path, source, blocks)

    assert callable_named(metrics, "probe")["max_nesting_depth"] == 2


def test_nested_functions_are_measured_separately(tmp_path):
    source = (
        "def outer():\n"
        "    def inner():\n"
        "        if True:\n"
        "            while True:\n"
        "                pass\n"
        "    return inner\n"
    )
    inner = {
        "type": "function",
        "name": "inner",
        "lineno": 2,
        "endline": 5,
        "complexity": 3,
    }
    blocks = [
        {
            "type": "function",
            "name": "outer",
            "lineno": 1,
            "endline": 6,
            "complexity": 1,
            "closures": [inner],
        },
    ]

    metrics = analyze_source(tmp_path, source, blocks)

    assert callable_named(metrics, "outer")["max_nesting_depth"] == 0
    assert callable_named(metrics, "outer.inner")["max_nesting_depth"] == 2


def test_method_identity_class_span_and_density(tmp_path):
    source = (
        "class Example:\n    def method(self):\n        if self:\n            pass\n"
    )
    method = {
        "type": "method",
        "name": "method",
        "classname": "Example",
        "lineno": 2,
        "endline": 4,
        "complexity": 2,
    }
    blocks = [
        {
            "type": "class",
            "name": "Example",
            "lineno": 1,
            "endline": 4,
            "complexity": 1,
            "methods": [method],
        },
    ]

    metrics = analyze_source(tmp_path, source, blocks)

    assert callable_named(metrics, "Example.method") == {
        "path": "src/ofmhelpers/probe.py",
        "name": "Example.method",
        "kind": "method",
        "lineno": 2,
        "endline": 4,
        "line_span": 3,
        "complexity": 2,
        "complexity_per_100_lines": 66.667,
        "max_nesting_depth": 1,
    }
    assert metrics["classes"] == [
        {
            "path": "src/ofmhelpers/probe.py",
            "name": "Example",
            "lineno": 1,
            "endline": 4,
            "line_span": 4,
            "complexity": 1,
        },
    ]


def analyze_project(tmp_path, sources, radon=None, maintainability=None):
    root = tmp_path / "project"
    for relative, source in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    if radon is None:
        radon = {relative: [] for relative in sources}
    radon_path = tmp_path / "radon-cc.json"
    radon_path.write_text(json.dumps(radon), encoding="utf-8")
    output = tmp_path / "output"
    arguments = [str(root), str(radon_path), str(output)]
    if maintainability is not None:
        mi_path = tmp_path / "radon-mi.json"
        mi_path.write_text(json.dumps(maintainability), encoding="utf-8")
        arguments.append(str(mi_path))
    assert quality_metrics.main(arguments) == 0
    return output


def test_dependency_graph_and_entropy_cover_all_areas(tmp_path):
    output = analyze_project(
        tmp_path,
        {
            "src/ofmhelpers/__init__.py": "",
            "src/ofmhelpers/one.py": (
                "import ofmhelpers.two\n"
                "import ofmhelpers.two\n"
                "from ofmhelpers import three\n"
                "import requests\n"
            ),
            "src/ofmhelpers/two.py": "",
            "src/ofmhelpers/three.py": ("from . import two\nfrom .four import value\n"),
            "src/ofmhelpers/four.py": "",
            "tests/test_sample.py": "from ofmhelpers import one\n",
            "alembic/env.py": "import ofmhelpers.two\n",
        },
    )

    metrics = json.loads((output / "custom-metrics.json").read_text(encoding="utf-8"))
    modules = {
        record["module"]: record for record in metrics["dependencies"]["modules"]
    }
    assert modules["ofmhelpers.one"]["targets"] == [
        "ofmhelpers.three",
        "ofmhelpers.two",
    ]
    assert modules["ofmhelpers.one"]["dependency_count"] == 2
    assert modules["ofmhelpers.one"]["entropy_bits"] == 1
    assert modules["ofmhelpers.two"]["dependency_count"] == 0
    assert modules["ofmhelpers.two"]["entropy_bits"] == 0
    assert modules["tests.test_sample"]["dependency_count"] == 1
    assert modules["tests.test_sample"]["entropy_bits"] == 0
    assert "requests" not in modules["ofmhelpers.one"]["targets"]
    areas = {record["area"]: record for record in metrics["dependencies"]["areas"]}
    assert set(areas) == {"ofmhelpers", "tests", "alembic"}
    assert areas["ofmhelpers"]["dependency_count"] == 3
    assert areas["ofmhelpers"]["entropy_bits"] == pytest.approx(1.584962500721156)
    assert areas["tests"]["entropy_bits"] == 0
    assert areas["alembic"]["entropy_bits"] == 0


def test_invalid_source_is_named(tmp_path):
    root = tmp_path / "project"
    broken = root / "src" / "ofmhelpers" / "broken.py"
    broken.parent.mkdir(parents=True)
    broken.write_text("def broken(:\n", encoding="utf-8")
    radon_path = tmp_path / "radon-cc.json"
    radon_path.write_text(
        json.dumps({"src/ofmhelpers/broken.py": {"error": "invalid syntax"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        quality_metrics.main([str(root), str(radon_path), str(tmp_path / "output")])

    assert "src/ofmhelpers/broken.py" in str(error.value)


def test_reports_rank_offenders_and_include_low_maintainability(tmp_path):
    source = (
        "def small():\n"
        "    if True:\n"
        "        pass\n"
        "\n"
        "def large():\n"
        "    value = 0\n"
        "    value += 1\n"
        "    value += 2\n"
        "    value += 3\n"
        "    if value:\n"
        "        while value:\n"
        "            pass\n"
    )
    radon = {
        "src/ofmhelpers/probe.py": [
            {
                "type": "function",
                "name": "small",
                "lineno": 1,
                "endline": 3,
                "complexity": 5,
            },
            {
                "type": "function",
                "name": "large",
                "lineno": 5,
                "endline": 12,
                "complexity": 2,
            },
        ],
    }
    output = analyze_project(
        tmp_path,
        {"src/ofmhelpers/probe.py": source},
        radon=radon,
        maintainability={"src/ofmhelpers/probe.py": {"mi": 12.345, "rank": "C"}},
    )

    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    offenders = summary["offenders"]
    assert offenders["complexity"][0]["name"] == "small"
    assert offenders["block_size"][0]["name"] == "large"
    assert offenders["density"][0]["name"] == "small"
    assert offenders["nesting"][0]["name"] == "large"
    assert offenders["maintainability"] == [
        {"path": "src/ofmhelpers/probe.py", "mi": 12.345, "rank": "C"},
    ]


def test_empty_module_missing_from_radon_is_allowed(tmp_path):
    output = analyze_project(
        tmp_path,
        {"src/ofmhelpers/__init__.py": ""},
        radon={},
    )

    metrics = json.loads((output / "custom-metrics.json").read_text(encoding="utf-8"))
    assert metrics["callables"] == []
    assert metrics["classes"] == []
    assert metrics["dependencies"]["modules"][0]["module"] == "ofmhelpers"


def test_callable_omitted_by_radon_keeps_ast_metrics_with_null_complexity(tmp_path):
    source = (
        "def outer():\n"
        "    class Counted:\n"
        "        def answered(self):\n"
        "            if self:\n"
        "                pass\n"
    )
    radon = {
        "src/ofmhelpers/probe.py": [
            {
                "type": "function",
                "name": "outer",
                "lineno": 1,
                "endline": 5,
                "complexity": 1,
            },
        ],
    }

    output = analyze_project(
        tmp_path,
        {"src/ofmhelpers/probe.py": source},
        radon=radon,
    )

    metrics = json.loads((output / "custom-metrics.json").read_text(encoding="utf-8"))
    nested = callable_named(metrics, "outer.Counted.answered")
    assert nested["line_span"] == 3
    assert nested["max_nesting_depth"] == 1
    assert nested["complexity"] is None
    assert nested["complexity_per_100_lines"] is None
    assert nested["kind"] == "method"
    assert metrics["classes"] == [
        {
            "path": "src/ofmhelpers/probe.py",
            "name": "outer.Counted",
            "lineno": 2,
            "endline": 5,
            "line_span": 4,
            "complexity": None,
        },
    ]
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["offenders"]["complexity"][0]["name"] == "outer"
    assert summary["offenders"]["nesting"][0]["name"] == "outer.Counted.answered"


def test_else_with_an_indented_if_adds_nesting(tmp_path):
    source = (
        "def probe(value):\n"
        "    if value:\n"
        "        pass\n"
        "    else:\n"
        "        if value:\n"
        "            pass\n"
    )
    blocks = [
        {
            "type": "function",
            "name": "probe",
            "lineno": 1,
            "endline": 6,
            "complexity": 2,
        },
    ]

    metrics = analyze_source(tmp_path, source, blocks)

    assert callable_named(metrics, "probe")["max_nesting_depth"] == 2


def test_absolute_test_module_import_uses_tests_root(tmp_path):
    output = analyze_project(
        tmp_path,
        {
            "tests/repo_paths.py": "",
            "tests/unit/scripts/test_quality_sh.py": "from repo_paths import REPO\n",
        },
    )

    metrics = json.loads((output / "custom-metrics.json").read_text(encoding="utf-8"))
    modules = {
        record["module"]: record for record in metrics["dependencies"]["modules"]
    }
    assert modules["tests.unit.scripts.test_quality_sh"]["targets"] == [
        "tests.repo_paths",
    ]
    assert modules["tests.unit.scripts.test_quality_sh"]["entropy_bits"] == 0


def test_radon_one_line_endline_uses_ast_span_for_multiline_callable(tmp_path):
    source = "def probe():\n    value = 0\n    if value:\n        pass\n"
    radon = {
        "src/ofmhelpers/probe.py": [
            {
                "type": "function",
                "name": "probe",
                "lineno": 1,
                "endline": 1,
                "complexity": 2,
            },
        ],
    }

    output = analyze_project(
        tmp_path,
        {"src/ofmhelpers/probe.py": source},
        radon=radon,
    )

    metrics = json.loads((output / "custom-metrics.json").read_text(encoding="utf-8"))
    probe = callable_named(metrics, "probe")
    assert probe["endline"] == 4
    assert probe["line_span"] == 4
    assert probe["complexity_per_100_lines"] == 50.0
