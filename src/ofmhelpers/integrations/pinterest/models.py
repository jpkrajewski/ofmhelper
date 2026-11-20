from typing import Any

from pydantic import BaseModel, ConfigDict, Field

UPLOADED = "succeeded"


class ResourceError(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = ""
    code: int | None = None
    http_status: int | None = None


class ResourceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = ""
    message: str = ""
    data: Any = None
    error: ResourceError | None = None


class Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    resource_response: ResourceResponse


class PinterestAccount(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    username: str


class PinterestBoard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    privacy: str = "public"


class UploadSlot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    upload_id: str
    upload_url: str
    upload_parameters: dict[str, str] = Field(default_factory=dict)


class UploadState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    upload_id: str
    status: str = ""
    signature: str | None = None
    failure_code: object = None

    @property
    def done(self) -> bool:
        return self.status == UPLOADED and bool(self.signature)

    @property
    def failed(self) -> bool:
        return self.status == "failed" or self.failure_code not in (None, 0, "")


class Media(BaseModel):
    signature: str
    width: int
    height: int
    video: bool = False
    cover_signature: str | None = None


class PinDraft(BaseModel):
    board_id: str
    media: Media
    title: str = ""
    description: str = ""
    link: str = ""


class CreatedPin(BaseModel):
    id: str

    @property
    def url(self) -> str:
        return f"https://www.pinterest.com/pin/{self.id}/"


class _BlockStyle(BaseModel):
    height: int = 100
    width: int = 100
    x_coord: int = 0
    y_coord: int = 0


class _Block(BaseModel):
    block_style: _BlockStyle = Field(default_factory=_BlockStyle)
    tracking_id: str
    type: int
    image_signature: str | None = None
    video_signature: str | None = None


class _Clip(BaseModel):
    clip_type: int
    end_time_ms: int = -1
    is_converted_from_image: bool = False
    source_media_height: int
    source_media_width: int
    start_time_ms: int = -1


class _Page(BaseModel):
    blocks: list[_Block]
    clips: list[_Clip]
    layout: int = 0
    style: dict[str, str] = Field(
        default_factory=lambda: {"background_color": "#FFFFFF"}
    )


class _StoryMeta(BaseModel):
    pin_title: str
    pin_image_signature: str
    canvas_aspect_ratio: float


class _Story(BaseModel):
    metadata: _StoryMeta
    pages: list[_Page]


class PinCreate(BaseModel):
    alt_text: str = ""
    allow_shopping_rec: bool = True
    board_id: str
    description: str
    fields: list[str] = Field(
        default_factory=lambda: [
            "pin.id",
            "pin.image_signature",
            "pin.story_pin_data_id",
        ]
    )
    is_comments_allowed: bool = True
    is_unified_builder: bool = True
    link: str
    method: str = "uploaded"
    story_pin: str
    user_mention_tags: str = "[]"

    @classmethod
    def of(cls, draft: PinDraft, tracking_id: str) -> PinCreate:
        m = draft.media
        if m.video:
            block = _Block(tracking_id=tracking_id, type=3, video_signature=m.signature)
        else:
            block = _Block(tracking_id=tracking_id, type=2, image_signature=m.signature)
        story = _Story(
            metadata=_StoryMeta(
                pin_title=draft.title,
                pin_image_signature=m.cover_signature or m.signature,
                canvas_aspect_ratio=m.width / m.height,
            ),
            pages=[
                _Page(
                    blocks=[block],
                    clips=[
                        _Clip(
                            clip_type=1 if m.video else 0,
                            source_media_height=m.height,
                            source_media_width=m.width,
                        )
                    ],
                )
            ],
        )
        return cls(
            board_id=draft.board_id,
            description=draft.description,
            link=draft.link,
            story_pin=story.model_dump_json(exclude_none=True),
        )
