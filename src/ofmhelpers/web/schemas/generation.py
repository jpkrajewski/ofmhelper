"""What a generation form posts.

The three reference-file pickers (images, videos, audio) are the one shape the
generation routers genuinely share -- seedance, fake_ai and replicate all take
the same six fields, and each used to repeat the same six-line parameter block
plus the same three `x or []` lines. kling and nbp take a single, differently
*named* list each (`images`, `image_input`), and those names are contract with
the templates and `file-picker.js`, so they stay declared in their own router
rather than being bent into this shape.

Data only: turning a manifest plus a set of uploads into paths on disk is
`routers/task_helpers.resolve_reference_uploads`, because that is where the
asset store lives.
"""

from typing import Annotated

from fastapi import File, Form, UploadFile
from pydantic import BaseModel, ConfigDict


class ReferenceUploads(BaseModel):
    """The ordered, reusable reference lists a generation form submits.

    Each picker posts two fields: the newly-chosen files, and a JSON manifest
    naming every entry in order (including ones already in the asset store, so
    a reused file is never re-uploaded -- see `build_ordered_paths`).

    Use as a FastAPI dependency: `refs: Annotated[ReferenceUploads,
    Depends(ReferenceUploads.from_form)]`.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    images: list[UploadFile] = []
    images_manifest: str = "[]"
    videos: list[UploadFile] = []
    videos_manifest: str = "[]"
    audio: list[UploadFile] = []
    audio_manifest: str = "[]"

    @classmethod
    def from_form(
        cls,
        reference_images: Annotated[list[UploadFile] | None, File()] = None,
        reference_images_manifest: Annotated[str, Form()] = "[]",
        reference_videos: Annotated[list[UploadFile] | None, File()] = None,
        reference_videos_manifest: Annotated[str, Form()] = "[]",
        reference_audio: Annotated[list[UploadFile] | None, File()] = None,
        reference_audio_manifest: Annotated[str, Form()] = "[]",
    ) -> "ReferenceUploads":
        """Starlette gives `None`, not `[]`, for a picker the user left empty;
        collapsing that here is what removes the three `if x is None` lines
        every one of these routers opened with."""
        return cls(
            images=reference_images or [],
            images_manifest=reference_images_manifest,
            videos=reference_videos or [],
            videos_manifest=reference_videos_manifest,
            audio=reference_audio or [],
            audio_manifest=reference_audio_manifest,
        )
