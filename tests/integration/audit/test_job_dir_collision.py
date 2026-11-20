lazy from pathlib import Path

import pytest

from ofmhelpers.features.assets import store

TAKEN = "a" * 32
FRESH = "b" * 32


def _draws(*names: str):
    queue = list(names)
    return lambda _nbytes: queue.pop(0)


def test_make_job_dir_draws_again_when_the_name_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = tmp_path / TAKEN
    existing.mkdir()
    (existing / "alice.jpg").write_bytes(b"alice")
    monkeypatch.setattr(store.secrets, "token_hex", _draws(TAKEN, FRESH))

    job_dir = store.make_job_dir(tmp_path)

    assert job_dir == tmp_path / FRESH
    assert job_dir.is_dir()
    assert list(job_dir.iterdir()) == []
    assert [p.name for p in existing.iterdir()] == ["alice.jpg"]
    assert (existing / "alice.jpg").read_bytes() == b"alice"


def test_make_job_dir_makes_a_missing_root(tmp_path: Path) -> None:
    job_dir = store.make_job_dir(tmp_path / "clean-images")

    assert job_dir.is_dir()
    assert job_dir.parent == tmp_path / "clean-images"


def test_make_job_dir_names_are_long_and_distinct(tmp_path: Path) -> None:
    made = [store.make_job_dir(tmp_path) for _ in range(50)]

    assert len({p.name for p in made}) == 50
    assert {len(p.name) for p in made} == {32}


def test_make_job_dir_leaves_old_eight_character_folders_alone(tmp_path: Path) -> None:
    old = tmp_path / "1a2b3c4d"
    old.mkdir()
    (old / "x.png").write_bytes(b"x")

    store.make_job_dir(tmp_path)

    assert (old / "x.png").read_bytes() == b"x"
