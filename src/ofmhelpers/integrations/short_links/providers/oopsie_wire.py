from typing import Any

from pydantic import BaseModel, Field, RootModel

from ofmhelpers.integrations.short_links.providers.base import RequestHeaders


class OopsieHeaders(RequestHeaders):
    api_key: str = Field(serialization_alias="x-api-key")


class OopsiePages(RootModel[list[dict[str, Any]]]):
    pass


class OopsiePageData(BaseModel, extra="allow"):
    theme: dict[str, Any] | None = None


class OopsieUsage(BaseModel):
    pages_used: int
    is_unlimited: bool | None = None
    effective_limit: int | None = None


class OopsieSeries(BaseModel):
    slug: str | None = None
    views: list[int]
    clicks: list[int]


class OopsieSlugActivity(BaseModel):
    labels: list[str]
    series: OopsieSeries | list[OopsieSeries]


class OopsieActivity(BaseModel):
    per_slug: OopsieSlugActivity | None = Field(default=None, alias="perSlug")
    time_series: OopsieSlugActivity | None = Field(default=None, alias="timeSeries")


class ActivityPoint(BaseModel, frozen=True):
    label: str
    views: int
    clicks: int


class LinkActivity(BaseModel):
    slug: str
    points: list[ActivityPoint]
