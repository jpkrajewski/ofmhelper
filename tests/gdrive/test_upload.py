from ofmhelpers.gdrive import upload


class _Call:
    def __init__(self, answer):
        self._answer = answer

    def execute(self):
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
