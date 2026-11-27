# Copyright (C) 2026 jpkrajewski

from ofmhelpers.features.files.schema import DirEntry
from ofmhelpers.features.kinds import FileKind


class FileEntry(DirEntry):
    size: str
    kind: FileKind = FileKind.OTHER
