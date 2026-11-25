# Copyright (C) 2026 jpkrajewski

from pathlib import Path
from typing import Annotated, BinaryIO, Self

from fastapi import UploadFile
from pydantic import AfterValidator
lazy from starlette.datastructures import Headers
lazy from starlette.datastructures import UploadFile as StarletteUploadFile

from ofmhelpers.config import settings
from ofmhelpers.features.assets.store import (
    jpeg_from_heic_or_current,
    require_upload_kind,
    safe_filename,
    validate_image,
)
from ofmhelpers.features.kinds import HEIC_EXTENSIONS, FileKind
from ofmhelpers.features.refusals import AssetRefusedError


class ValidatedSanitizedMediaUpload(UploadFile):
    def __init__(
        self,
        file: BinaryIO,
        *,
        size: int,
        filename: str,
        kind: FileKind,
        headers: Headers,
    ) -> None:
        super().__init__(file=file, size=size, filename=filename, headers=headers)
        self.kind = kind

    @classmethod
    def validated(
        cls,
        upload: StarletteUploadFile,
        kinds: frozenset[FileKind],
    ) -> Self:
        position = upload.file.tell()
        try:
            upload.file.seek(0, 2)
            size = upload.file.tell()
        finally:
            upload.file.seek(position)
        if size == 0:
            msg = "File is empty"
            raise AssetRefusedError(msg)
        max_upload_mb = settings.web.max_upload_mb
        if size > max_upload_mb * 1024 * 1024:
            msg = f"File exceeds the {max_upload_mb} MB upload limit"
            raise AssetRefusedError(msg)
        upload.filename = safe_filename(upload.filename)
        kind = FileKind.from_ext(upload.filename)
        require_upload_kind(kind, kinds)
        if kind == FileKind.IMAGE:
            path = Path(upload.filename)
            upload.file = jpeg_from_heic_or_current(upload.file, path)
            if path.suffix.lower() in HEIC_EXTENSIONS:
                upload.filename = path.with_suffix(".jpg").name
            upload.file = validate_image(upload.file)
        return cls(
            file=upload.file,
            size=size,
            filename=upload.filename,
            kind=kind,
            headers=upload.headers,
        )


def validate_file_upload(upload: StarletteUploadFile) -> ValidatedSanitizedMediaUpload:
    return ValidatedSanitizedMediaUpload.validated(upload, frozenset(FileKind))


def validate_image_upload(upload: StarletteUploadFile) -> ValidatedSanitizedMediaUpload:
    return ValidatedSanitizedMediaUpload.validated(upload, frozenset({FileKind.IMAGE}))


def validate_video_upload(upload: StarletteUploadFile) -> ValidatedSanitizedMediaUpload:
    return ValidatedSanitizedMediaUpload.validated(upload, frozenset({FileKind.VIDEO}))


def validate_audio_upload(upload: StarletteUploadFile) -> ValidatedSanitizedMediaUpload:
    return ValidatedSanitizedMediaUpload.validated(upload, frozenset({FileKind.AUDIO}))


def validate_media_upload(upload: StarletteUploadFile) -> ValidatedSanitizedMediaUpload:
    return ValidatedSanitizedMediaUpload.validated(
        upload,
        frozenset({FileKind.IMAGE, FileKind.VIDEO, FileKind.AUDIO}),
    )


FileUpload = Annotated[UploadFile, AfterValidator(validate_file_upload)]
ImageUpload = Annotated[UploadFile, AfterValidator(validate_image_upload)]
VideoUpload = Annotated[UploadFile, AfterValidator(validate_video_upload)]
AudioUpload = Annotated[UploadFile, AfterValidator(validate_audio_upload)]
MediaUpload = Annotated[UploadFile, AfterValidator(validate_media_upload)]
