# Copyright (C) 2026 jpkrajewski

import os
import time

from ofmhelpers.features.viral import cleanup


def _folder(root, name, age_s):
    folder = root / name
    folder.mkdir(parents=True)
    reel = folder / "a.mp4"
    reel.write_bytes(b"x")
    then = time.time() - age_s
    os.utime(reel, (then, then))
    os.utime(folder, (then, then))
    return folder


def test_search_folders_older_than_three_days_go_and_newer_stay(tmp_path, monkeypatch):
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    root = tmp_path / cleanup.FOLDER
    old = _folder(root, "old", 3 * 86400 + 3600)
    fresh = _folder(root, "fresh", 86400)
    assert cleanup.remove_old() == 1
    assert not old.exists()
    assert fresh.exists()
