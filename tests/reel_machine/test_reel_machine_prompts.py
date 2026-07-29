"""
Tuning the analysis prompt is a read-the-bad-output-and-rewrite-a-sentence
loop, and prod runs the code baked into the image -- so the prompt is
overridable by a file under the bind-mounted uploads/ dir. What matters is
that a present file wins, an absent or empty one falls back rather than
sending a blank prompt, and the change lands without a restart.
"""

from ofmhelpers.reel_machine.prompts import (
    DEFAULT_ANALYSIS_PROMPT,
    load_analysis_prompt,
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
