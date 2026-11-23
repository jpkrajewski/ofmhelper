# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import Response

from ofmhelpers.features.distribution import service
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, CurrentUser, Scope, Storage
from ofmhelpers.web.responses import NO_STORE
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/distribution",
    tags=["distribution"],
    dependencies=[Access(Area.DISTRIBUTION)],
)


def _distribution(
    user: CurrentUser,
    scope: Scope,
    storage: Storage,
) -> service.Distribution:
    return service.Distribution(uid=user.id, scope=scope, storage=storage)


def _uploads(files: list[UploadFile]) -> list[service.Upload]:
    return [
        service.Upload(name=f.filename or "cookies.txt", file=f.file) for f in files
    ]


Distribution = Annotated[service.Distribution, Depends(_distribution)]
Uploads = Annotated[list[service.Upload], Depends(_uploads)]


def _page(
    request: Request,
    page: service.DistributionPage,
    status_code: int = 200,
) -> Response:
    response = render(
        request,
        "distribution.html",
        {"page": page},
        status_code=status_code,
    )
    response.headers.update(NO_STORE)
    return response


@router.get("")
def distribution_page(request: Request, dist: Distribution) -> Response:
    return _page(request, dist.page())


@router.post("/accounts")
def add_accounts(
    request: Request,
    dist: Distribution,
    uploads: Uploads,
) -> Response:
    return _page(request, dist.page(added=dist.add(uploads)))
