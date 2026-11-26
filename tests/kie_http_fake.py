# Copyright (C) 2026 jpkrajewski

from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock

import requests


@contextmanager
def kie_http():
    with (
        mock.patch.object(requests.Session, "request") as request,
        mock.patch(
            "ofmhelpers.integrations.ai_providers.kie.client.requests.head",
        ) as head,
    ):
        yield SimpleNamespace(post=request, request=request, head=head)


def response(body: dict, status_code: int = 200) -> mock.Mock:
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = str(body)
    return resp
