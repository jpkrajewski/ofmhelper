from ofmhelpers.gdrive import upload


class _Call:
    def __init__(self, answer):
        self._answer = answer

    def execute(self, **_kwargs):
        return self._answer


class _Files:
    def __init__(self, existing):
        self.existing = existing
        self.queries = []
        self.created = []

    def list(self, q, **_kwargs):
        self.queries.append(q)
        return _Call({"files": [{"id": i} for i in self.existing]})

    def create(self, body, **_kwargs):
        self.created.append(body)
        return _Call({"id": "new"})


class _Drive:
    def __init__(self, existing):
        self.files_api = _Files(existing)

    def files(self):
        return self.files_api


def test_reuses_the_apps_folder():
    drive = _Drive(["f1"])
    assert upload._folder_id(drive, "clones") == "f1"
    assert drive.files_api.created == []


def test_creates_the_folder_once_missing():
    drive = _Drive([])
    assert upload._folder_id(drive, "it's") == "new"
    assert "name = 'it\\'s'" in drive.files_api.queries[0]
    assert drive.files_api.created == [
        {"name": "it's", "mimeType": "application/vnd.google-apps.folder"}
    ]


def _fake_drive(monkeypatch, there, fail=()):
    uploaded = []
    monkeypatch.setattr(upload, "_drive", lambda _c: _Drive(["f1"]))
    monkeypatch.setattr(upload, "_names_in", lambda _d, _f: set(there))

    def one(_c, folder, path):
        uploaded.append((folder, path.name))
        return path.name not in fail

    monkeypatch.setattr(upload, "_upload_one", one)
    return uploaded


def test_upload_files_puts_every_file_in_the_folder(monkeypatch, tmp_path):
    uploaded = _fake_drive(monkeypatch, there=[])

    report = upload.upload_files("rt", [tmp_path / "a.mp4", tmp_path / "b.mp4"])

    assert report.folder_url == "https://drive.google.com/drive/folders/f1"
    assert sorted(uploaded) == [("f1", "a.mp4"), ("f1", "b.mp4")]
    assert report.uploaded == ["a.mp4", "b.mp4"]


def test_a_retry_skips_what_is_already_there(monkeypatch, tmp_path):
    uploaded = _fake_drive(monkeypatch, there=["a.mp4"], fail=["c.mp4"])

    report = upload.upload_files(
        "rt", [tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "c.mp4"]
    )

    assert sorted(uploaded) == [("f1", "b.mp4"), ("f1", "c.mp4")]
    assert report.already_there == ["a.mp4"]
    assert report.uploaded == ["b.mp4"]
    assert report.failed == ["c.mp4"]
