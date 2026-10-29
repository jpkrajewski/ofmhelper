"""The saver sites faked at the wire (OFM_FAKE_EXTERNALS, tests):
saveinta.com, snapreels.net and their shared dl.snapcdn.app file host,
answered the way the real ones answer (verified 2026-10-29,
downloaders/savers.py), so the real client runs unchanged. Nothing leaves
the process.

A reel answers one mp4, a photo post two jpgs, a link with
"fake-carousel" twelve (the one-post limit's bound), "fake-fail" the way
a removed post answers (status "fail"). saveinta's result comes as the
same packed script the live site sends; snapreels' as plain HTML.
"""

from __future__ import annotations

import urllib.parse
from urllib.parse import parse_qs, urlsplit

from ofmhelpers.utils import fake_media
from ofmhelpers.utils.outbound import FakeHandler, FakeReply

_K_EXP = "1893456000"  # far future; the real ones live ~a day
_K_EXPIRY_CHECK = "1893542400"  # the `if (epoch < ...)` guard in the payload
_K_TOKEN = "ab" * 32

_PAGE = (
    "<!DOCTYPE html><html><head><title>SnapReels</title></head><body>"
    '<form id="search-form" action=/en method=post>'
    "<input type=text name=q id=s_input></form>"
    '<script>k_url_search="{base}/api/ajaxSearch";k_exp="{exp}",'
    'k_token="{tok}";k_lang="en";k_verifytime=8e3</script>'
    "</body></html>"
)

# The real saveinta prelude + eval wrapper, verbatim, so a captured fake
# response decodes with the site's own tools too. The payload args mirror
# the live ones: charset, charcode offset, base.
_PRELUDE = (
    'var _0xc84e=["","split",'
    '"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/",'
    '"slice","indexOf","","",".","pow","reduce","reverse","0"];'
    "function _0xe48c(d,e,f){var g=_0xc84e[2][_0xc84e[1]](_0xc84e[0]);"
    "var h=g[_0xc84e[3]](0,e);var i=g[_0xc84e[3]](0,f);"
    "var j=d[_0xc84e[1]](_0xc84e[0])[_0xc84e[10]]()[_0xc84e[9]]"
    "(function(a,b,c){if(h[_0xc84e[4]](b)!==-1)"
    "return a+=h[_0xc84e[4]](b)*(Math[_0xc84e[8]](e,c))},0);"
    "var k=_0xc84e[0];while(j>0){k=i[j%f]+k;j=(j-(j%f))/f}return k||_0xc84e[11]}"
)
_EVAL = (
    'eval(function(h,u,n,t,e,r){r="";for(var i=0,len=h.length;i<len;i++)'
    '{var s="";while(h[i]!==n[e]){s+=h[i];i++}for(var j=0;j<n.length;j++)'
    's=s.replace(new RegExp(n[j],"g"),j);'
    "r+=String.fromCharCode(_0xe48c(s,e,10)-t)}return decodeURIComponent(r)}"
    '("__PACKED__","45","__CHARSET__","__OFFSET__","__RADIX__"))'
)

_CHARSET = "DNOBcTIwJ"  # a live response's charset; any distinct chars work
_RADIX = 8
_OFFSET = 22
_DELIMITER = _CHARSET[_RADIX]

_FAIL = "fake-fail"
_CAROUSEL = "fake-carousel"


def _pack(js: str) -> str:
    """The saveinta packing, inverted: URI-encode, then each charcode +
    offset as base-`_RADIX` digits over `_CHARSET`, blocks delimited by
    `_CHARSET[_RADIX]` (`savers._unpack` decodes exactly this)."""
    out = []
    for ch in urllib.parse.quote(js, safe=""):
        value = ord(ch) + _OFFSET
        block = ""
        while value > 0:
            block = _CHARSET[value % _RADIX] + block
            value //= _RADIX
        out.append(block + _DELIMITER)
    return "".join(out)


def _items(url: str) -> list[str]:
    """The dl tokens the post answers with: one mp4 per reel, two jpgs per
    photo post, twelve for the carousel marker."""
    segments = [s for s in urlsplit(url).path.split("/") if s]
    shortcode = segments[-1] if segments else "post"
    if _CAROUSEL in url:
        return [f"fake-{shortcode}-{i}.jpg" for i in range(1, 13)]
    if "/p/" in url:
        return [f"fake-{shortcode}-1.jpg", f"fake-{shortcode}-2.jpg"]
    return [f"fake-{shortcode}-1.mp4"]


def _result_html(url: str, host: str) -> str:
    brand = "SaveInsta" if "saveinta" in host else "SnapReels"
    items = _items(url)
    lis = []
    for token in items:
        ext = token.rsplit(".", 1)[-1]
        thumb = f"https://i.snapcdn.app/photo?token={token}"
        lis.append(
            '<li><div class="download-items">'
            f'<div class="download-items__thumb"><img src="{thumb}" alt="{brand}">'
            '<span class="format-icon"><i class="icon icon-dlvideo"></i></span></div>'
            '<div class="download-items__btn">'
            f'<a href="https://dl.snapcdn.app/get?token={token}">Download</a>'
            f"<span>{ext}</span></div></div></li>"
        )
    return '<div id="ig_page"><ul class="download-box">' + "".join(lis) + "</ul></div>"


def _ajax_search(host: str, body: bytes | str) -> FakeReply:
    raw = body.decode() if isinstance(body, bytes) else (body or "")
    fields = parse_qs(raw)
    url = fields.get("q", [""])[0]
    if _FAIL in url:
        return FakeReply.json_body(
            {"status": "fail", "mess": "No data found. Please try again!"}
        )
    html = _result_html(url, host)
    if "saveinta" in host:
        escaped = html.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")
        js = (
            f"if ((Math.round(+new Date() / 1000)) < {_K_EXPIRY_CHECK}){{\n"
            f"if (window.location.hostname === '{host}'){{\n"
            'document.getElementById("search-result").innerHTML = "'
            f'{escaped}";}}\n}}'
        )
        packed = _pack(js)
        data = _PRELUDE + _EVAL.replace("__PACKED__", packed).replace(
            "__CHARSET__", _CHARSET
        ).replace("__OFFSET__", str(_OFFSET)).replace("__RADIX__", str(_RADIX))
        return FakeReply.json_body(
            {"status": "ok", "p": "instagram", "v": "v2", "data": data}
        )
    return FakeReply.json_body({"status": "ok", "p": "instagram", "data": html})


def _dl(query: str) -> FakeReply:
    token = parse_qs(query).get("token", [""])[0]
    if token.endswith(".mp4"):
        return FakeReply(
            status=200, body=fake_media.mp4(), headers={"Content-Type": "video/mp4"}
        )
    return FakeReply(
        status=200,
        body=fake_media.image("salmon"),
        headers={"Content-Type": "image/jpeg"},
    )


def _saver_host(host: str):
    def handle(request, _proxies) -> FakeReply:
        split = urlsplit(request.url or "")
        if request.method == "GET" and split.path.rstrip("/") in ("", "/en"):
            page = _PAGE.format(base=f"https://{host}", exp=_K_EXP, tok=_K_TOKEN)
            return FakeReply.text(page)
        if request.method == "POST" and split.path == "/api/ajaxSearch":
            return _ajax_search(host, request.body or b"")
        return FakeReply.text("not found", status=404)

    return handle


def _snapcdn(request, _proxies) -> FakeReply:
    split = urlsplit(request.url or "")
    if request.method == "GET" and split.hostname == "dl.snapcdn.app":
        return _dl(split.query)
    return FakeReply(
        status=200,
        body=fake_media.image("steelblue"),
        headers={"Content-Type": "image/jpeg"},
    )


def routes() -> dict[str, FakeHandler]:
    return {
        "saveinta.com": _saver_host("saveinta.com"),
        "snapreels.net": _saver_host("snapreels.net"),
        "dl.snapcdn.app": _snapcdn,
    }
