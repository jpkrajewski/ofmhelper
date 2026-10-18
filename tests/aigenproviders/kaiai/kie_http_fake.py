"""Patch kie.ai's HTTP at the wire: every client call goes through one
`requests.Session.request` (uploads and the task queue) plus `requests.head`
(the upload cache's liveness check). `mreq.post` is that session call, named
for what the upload tests send through it."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

import requests


@contextmanager
def kie_http():
    with (
        mock.patch.object(requests.Session, "request") as request,
        mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests.head") as head,
    ):
        yield SimpleNamespace(post=request, request=request, head=head)


def response(body: dict, status_code: int = 200) -> mock.Mock:
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = str(body)
    return resp
