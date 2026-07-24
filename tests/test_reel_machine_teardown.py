"""
build_teardown_draft groups a transcript's word-level timing into beats by
pause length -- the same "word-level gaps are the pause structure" idea the
old reel-machine bundle's reel-intake skill used by hand. These tests pin
down that heuristic.
"""

from ofmhelpers.reel_machine.intake import Transcript, Word
from ofmhelpers.reel_machine.teardown import build_teardown_draft


def test_groups_words_into_beats_by_pause():
    words = [
        Word("Hi", 0.0, 0.3),
        Word("there", 0.3, 0.6),
        Word("Big", 1.5, 1.8),  # gap since previous word's end (0.6) >= BEAT_GAP_S
        Word("reveal", 1.8, 2.2),
    ]
    transcript = Transcript(text="Hi there Big reveal", words=words)

    teardown = build_teardown_draft(transcript, duration=15.0)

    assert [b.text for b in teardown.beats] == ["Hi there", "Big reveal"]
    assert teardown.hook == "Hi there"


def test_short_gap_stays_in_the_same_beat():
    words = [
        Word("Hi", 0.0, 0.3),
        Word("there", 0.35, 0.6),  # 0.05s gap -- well under BEAT_GAP_S
    ]
    transcript = Transcript(text="Hi there", words=words)

    teardown = build_teardown_draft(transcript, duration=15.0)

    assert len(teardown.beats) == 1
    assert teardown.beats[0].text == "Hi there"


def test_empty_transcript_produces_a_placeholder_beat():
    teardown = build_teardown_draft(Transcript(text="", words=[]), duration=15.0)

    assert len(teardown.beats) == 1
    assert "no words transcribed" in teardown.beats[0].text
    assert teardown.beats[0].end == 15.0
