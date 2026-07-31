"""
Tuning a prompt is a read-the-bad-output-and-rewrite-a-sentence loop, and prod
runs the code baked into the image -- so every prompt is overridable by a file
under the bind-mounted uploads/ dir. What matters is that a present file wins,
an absent or empty one falls back rather than sending a blank prompt, and the
change lands without a restart.
"""

import pytest

from ofmhelpers.reel_machine.prompts import (
    CONTEXT_HEADER,
    DEFAULT_ANALYSIS_PROMPT,
    DEFAULT_ANALYSIS_SYSTEM_PROMPT,
    DEFAULT_HUNT_PROMPT,
    load_analysis_prompt,
    load_analysis_system_prompt,
    load_hunt_prompt,
)


def test_uses_the_built_in_prompt_when_there_is_no_override(monkeypatch, tmp_path):
    monkeypatch.setenv("REEL_MACHINE_PROMPT_FILE", str(tmp_path / "nope.txt"))
    assert load_analysis_prompt() == DEFAULT_ANALYSIS_PROMPT


def test_an_override_file_wins(monkeypatch, tmp_path):
    path = tmp_path / "analysis_prompt.txt"
    path.write_text("describe the reel, briefly\n", encoding="utf-8")
    monkeypatch.setenv("REEL_MACHINE_PROMPT_FILE", str(path))

    assert load_analysis_prompt() == "describe the reel, briefly"


def test_an_edit_takes_effect_without_a_restart(monkeypatch, tmp_path):
    """The whole point of the file: edit it on the server and the next job
    uses it. Read per call, never cached at import."""
    path = tmp_path / "analysis_prompt.txt"
    path.write_text("first", encoding="utf-8")
    monkeypatch.setenv("REEL_MACHINE_PROMPT_FILE", str(path))
    assert load_analysis_prompt() == "first"

    path.write_text("second", encoding="utf-8")
    assert load_analysis_prompt() == "second"


def test_an_empty_override_falls_back(monkeypatch, tmp_path):
    """Sending a blank prompt would burn a real API call on nothing."""
    path = tmp_path / "analysis_prompt.txt"
    path.write_text("   \n", encoding="utf-8")
    monkeypatch.setenv("REEL_MACHINE_PROMPT_FILE", str(path))

    assert load_analysis_prompt() == DEFAULT_ANALYSIS_PROMPT


def test_context_lands_at_the_very_end(monkeypatch, tmp_path):
    """The prompt ends with the JSON template's closing brace -- context spliced
    in anywhere earlier reads as part of the shape being asked for."""
    monkeypatch.setenv("REEL_MACHINE_PROMPT_FILE", str(tmp_path / "nope.txt"))
    prompt = load_analysis_prompt("she is a gym influencer")

    assert prompt.startswith(DEFAULT_ANALYSIS_PROMPT)
    assert prompt.endswith("she is a gym influencer")
    assert CONTEXT_HEADER in prompt


def test_an_empty_context_leaves_the_prompt_byte_identical(monkeypatch, tmp_path):
    monkeypatch.setenv("REEL_MACHINE_PROMPT_FILE", str(tmp_path / "nope.txt"))
    assert load_analysis_prompt("") == DEFAULT_ANALYSIS_PROMPT
    assert load_analysis_prompt("   \n ") == DEFAULT_ANALYSIS_PROMPT


def test_context_rides_on_the_override_file_too(monkeypatch, tmp_path):
    path = tmp_path / "analysis_prompt.txt"
    path.write_text("describe the reel", encoding="utf-8")
    monkeypatch.setenv("REEL_MACHINE_PROMPT_FILE", str(path))

    prompt = load_analysis_prompt("keep the cameraman male")
    assert prompt.startswith("describe the reel")
    assert prompt.endswith("keep the cameraman male")


# ---------------------------------------------------------------------------
# The other two prompts (the analysis system instruction and the second pass'),
# which live here rather than in a provider so they are retunable the same way.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_var", "loader", "default"),
    [
        (
            "REEL_MACHINE_SYSTEM_PROMPT_FILE",
            load_analysis_system_prompt,
            DEFAULT_ANALYSIS_SYSTEM_PROMPT,
        ),
        (
            "REEL_MACHINE_HUNT_PROMPT_FILE",
            lambda: load_hunt_prompt("{}", 6),
            DEFAULT_HUNT_PROMPT,
        ),
    ],
)
def test_every_prompt_is_overridable_and_falls_back(
    monkeypatch, tmp_path, env_var, loader, default
):
    path = tmp_path / "prompt.txt"
    monkeypatch.setenv(env_var, str(path))
    assert default[:30] in loader()

    path.write_text("do the thing", encoding="utf-8")
    assert loader() == "do the thing"

    path.write_text("  \n", encoding="utf-8")
    assert default[:30] in loader()


def test_the_hunt_prompt_carries_the_analysis_and_the_item_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("REEL_MACHINE_HUNT_PROMPT_FILE", str(tmp_path / "nope.txt"))

    prompt = load_hunt_prompt('{"context": "a starbucks skit"}', 4)

    assert '{"context": "a starbucks skit"}' in prompt
    assert "4 items per list" in prompt
    assert "{{ANALYSIS}}" not in prompt
    assert "{{MAX_ITEMS}}" not in prompt


def test_an_override_full_of_braces_is_not_a_format_string(monkeypatch, tmp_path):
    """These files are hand-edited on the server and the prompts they replace
    are full of literal `{` -- substitution is str.replace, never .format."""
    path = tmp_path / "hunt_prompt.txt"
    path.write_text('answer as {"topics": []} for {{ANALYSIS}}', encoding="utf-8")
    monkeypatch.setenv("REEL_MACHINE_HUNT_PROMPT_FILE", str(path))

    assert load_hunt_prompt("DIGEST", 6) == 'answer as {"topics": []} for DIGEST'
