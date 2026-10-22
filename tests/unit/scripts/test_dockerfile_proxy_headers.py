"""The image must trust nginx's forwarded headers.

Without them uvicorn keeps the request scheme at http behind the TLS proxy, so
`url_for` renders http:// asset links on an https page (mixed content) and the
rate limiter buckets every client under the proxy's IP.
"""

from repo_paths import REPO

DOCKERFILE = REPO / "Dockerfile"


def test_production_cmd_trusts_forwarded_headers() -> None:
    cmd = next(
        line
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("CMD [")
    )

    assert "--proxy-headers" in cmd
    assert "--forwarded-allow-ips" in cmd


def test_a_client_written_forwarded_for_is_not_its_address() -> None:
    """nginx appends the real peer to whatever X-Forwarded-For the client
    sent; "*" made uvicorn take the client's own (leftmost) entry, so a new
    made-up one per request escaped the failed-login limit."""
    from uvicorn.middleware.proxy_headers import _TrustedHosts

    cmd = next(
        line
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("CMD [")
    )
    trusted = cmd.split('"--forwarded-allow-ips", "')[1].split('"')[0]
    assert trusted != "*"
    hosts = _TrustedHosts(trusted)
    assert "172.18.0.1" in hosts  # prod's Docker gateway (nginx's hop)
    host, _ = hosts.get_trusted_client_address("6.6.6.6, 203.0.113.9")
    assert host == "203.0.113.9"
