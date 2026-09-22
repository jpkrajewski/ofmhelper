"""Pure text/URL classification: does this bio point at OnlyFans?

No network here on purpose -- everything in this file is a function of the
profile text alone, so it is cheap, deterministic and testable. The one
step that needs a network call (reading a link-aggregator page to see what
it points at) lives in aggregators.py.

Three ways a creator's OnlyFans shows up in a public bio, in descending
order of how sure it makes us:

1. A direct onlyfans.com URL, in the link field or typed into the bio.
   Also its official short domain (of.link) and the regional onlyfans.co.
2. A link-aggregator URL (linktr.ee, beacons.ai, ...). Most creators use
   one, because Instagram has historically throttled reach on bios that
   link onlyfans.com directly. The OnlyFans link is one hop away -- see
   aggregators.py.
3. The word itself, obfuscated or not ("0nlyfans", "only fans", "0F"),
   with no URL anywhere. Worth a row, never worth a CERTAIN.
"""

import re

# The aggregator hosts creators in this niche actually use. Matched on host
# substring, so a subdomain or a trailing path never hides one.
AGGREGATOR_HOSTS = (
    "linktr.ee",
    "beacons.ai",
    "allmylinks.com",
    "bio.link",
    "solo.to",
    "campsite.bio",
    "taplink.cc",
    "linkr.bio",
    "lnk.bio",
    "snipfeed.co",
    "stan.store",
    "direct.me",
    "komi.io",
    "hoo.be",
    "msha.ke",
    "carrd.co",
    "withkoji.com",
    "tap.bio",
    "flowcode.com",
    "linkin.bio",
)

# Bare-domain URLs are the norm in a bio ("linktr.ee/name", no scheme), so
# the scheme is optional and the host is what anchors the match.
_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9.-]*\.[a-z]{2,})(/[^\s,;)\]\"'<>]*)?",
    re.IGNORECASE,
)

_DIRECT_OF_HOSTS = ("onlyfans.com", "of.link", "onlyfans.co")

# Only the deliberate obfuscations creators actually type. The initialism is
# matched ONLY in its dotted form ("O.F"): a bare "OF" appears in half the
# English bios ever written ("photographer of light", and every ALL-CAPS bio
# ever), and each one would become a lead. The recall cost is small -- a
# creator who writes "my OF" almost always also carries a link or an
# aggregator, both of which are stronger signals already handled above.
_MENTION_RE = re.compile(
    r"(only\s*f[a4]ns|0nly\s*f[a4]ns|onlyf[a4]ns|\bo\.f\b)",
    re.IGNORECASE,
)


def _iter_urls(text: str) -> list[str]:
    out: list[str] = []
    for match in _URL_RE.finditer(text or ""):
        host, path = match.group(1).lower(), match.group(2) or ""
        out.append(f"https://{host}{path}")
    return out


def _host_of(url: str) -> str:
    return url.removeprefix("https://").split("/", 1)[0].lower()


def find_direct_of_url(text: str) -> str | None:
    """The first onlyfans.com-family URL in the text, normalised to https.
    None if there is none."""
    for url in _iter_urls(text):
        if any(_host_of(url).endswith(host) for host in _DIRECT_OF_HOSTS):
            return url
    return None


def find_aggregator_urls(text: str) -> list[str]:
    """Every link-aggregator URL in the text, in the order it appeared."""
    return [
        url
        for url in _iter_urls(text)
        if any(host in _host_of(url) for host in AGGREGATOR_HOSTS)
    ]


def mentions_onlyfans(text: str) -> bool:
    """The word, however it was spelled around Instagram's filters."""
    return bool(_MENTION_RE.search(text or ""))
