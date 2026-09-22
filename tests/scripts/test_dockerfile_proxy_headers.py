"""The image must trust nginx's forwarded headers.

Without them uvicorn keeps the request scheme at http behind the TLS proxy, so
`url_for` renders http:// asset links on an https page (mixed content) and the
rate limiter buckets every client under the proxy's IP.
"""

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def test_production_cmd_trusts_forwarded_headers() -> None:
    cmd = next(
        line
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("CMD [")
    )

    assert "--proxy-headers" in cmd
    assert "--forwarded-allow-ips" in cmd
