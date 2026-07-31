"""The two hunts the VA does by hand around a generation, pre-typed off the
analysis so the niche doesn't have to be retyped for every reel:

  - wardrobe: find an outfit that fits the niche, turn it into a clothes sheet
  - similar reels: find more of this format to clone next

Which engines are reachable is the whole subtlety here -- see `_REEL_ENGINES`
on why Instagram is searched through Google rather than directly.
"""

from urllib.parse import quote_plus

# The two hunts the VA does by hand between analysis and generation, pre-typed
# off the analysis so the niche doesn't have to be retyped every reel:
#   - wardrobe: find an outfit that fits the niche, turn it into a clothes sheet
#   - similar reels: find more of this format to clone next
_OUTFIT_ENGINES = (
    ("Pinterest", "https://www.pinterest.com/search/pins/?q={q}"),
    ("Google Images", "https://www.google.com/search?tbm=isch&q={q}"),
)


_REEL_ENGINES = (
    ("TikTok", "https://www.tiktok.com/search?q={q}"),
    # Instagram's own keyword search and hashtag pages are login-gated (they
    # stopped serving those logged out in 2024), which is why searching
    # Instagram from here was useless. Its reels ARE indexed publicly, so a
    # site: search reaches them without a login wall; the niche pages proper
    # are instagram.com/popular/<slug>, built from hunt.instagram_topics.
    (
        "Instagram via Google",
        "https://www.google.com/search?q=site%3Ainstagram.com%2Freel%2F+{q}",
    ),
)


# The niche pages: /popular/<slug>, e.g. /popular/baseball-girl, built from
# hunt.instagram_topics. Unlike /explore/tags/ and Instagram's own search
# these are not login-gated. Bare /popular/ is deliberately NOT linked -- with
# no slug it is a generic signed-out landing page, one more button to ignore.
_INSTAGRAM_TOPIC_URL = "https://www.instagram.com/popular/{slug}/"


_MAX_QUERY_CHARS = 120


_MIN_QUERY_CHARS = 3


def _shorten(text: str) -> str:
    """Collapse whitespace and cut to _MAX_QUERY_CHARS on a word boundary --
    the analysis' `context` is a sentence, and half a word at the end of a
    search query is just noise the engine has to guess at."""
    words = text.split()
    query = ""
    for word in words:
        candidate = f"{query} {word}".strip()
        if len(candidate) > _MAX_QUERY_CHARS:
            break
        query = candidate
    # A single word longer than the limit still has to be cut somewhere.
    return query or " ".join(words)[:_MAX_QUERY_CHARS]


def _searches(queries: list[str], engines: tuple[tuple[str, str], ...]) -> list[dict]:
    """Each usable query paired with one link per engine. Blank, too-short and
    duplicate queries drop out, so a half-filled analysis yields fewer rows
    rather than dead searches."""
    searches = []
    seen = set()
    for raw in queries:
        query = _shorten(raw)
        if len(query) < _MIN_QUERY_CHARS or query.lower() in seen:
            continue
        seen.add(query.lower())
        searches.append(
            {
                "query": query,
                "links": [
                    {"label": label, "url": url.format(q=quote_plus(query))}
                    for label, url in engines
                ],
            }
        )
    return searches


def _as_womens_outfit(query: str) -> str:
    """Pinterest and Google Images answer a bare clothing description with
    menswear and flat-lays as often as not; the reels being cloned are always
    a girl, so the query says so."""
    if not query.strip() or any(
        word in query.lower() for word in ("girl", "woman", "women")
    ):
        return query
    return f"girl {query}"


def _subject(prompt: dict) -> dict:
    """The person being cloned. Mirrors ReelAnalysis.subject: the entry with
    id "subject", else the first one. Everyone else in `people` is a cameraman
    or a passer-by, and their "wardrobe: not visible" is not something to go
    shopping for."""
    people = [p for p in (prompt.get("people") or []) if isinstance(p, dict)]
    for person in people:
        if person.get("id") == "subject":
            return person
    return people[0] if people else {}


def _outfit_searches(prompt: dict | None, hunt: dict | None = None) -> list[dict]:
    """Outfit searches for the main subject, best terms first: the free
    model's alternatives (see reel_machine/hunt.py) ahead of the ones derived
    straight from the analysis' setting and the subject's own wardrobe. The
    derived ones stay as the floor -- there is no key on every deployment, and
    old jobs have no hunt stored at all."""
    if not prompt:
        return []
    environment = (prompt.get("environment") or "").strip()
    return _searches(
        [
            _as_womens_outfit(idea)
            for idea in [
                *((hunt or {}).get("outfit_ideas") or []),
                f"{environment} outfit inspo" if environment else "",
                _subject(prompt).get("wardrobe") or "",
            ]
        ],
        _OUTFIT_ENGINES,
    )


def _reel_searches(prompt: dict | None, hunt: dict | None = None) -> list[dict]:
    """Searches for more reels like this one -- the free model's phrases first,
    then the ones derived from the analysis itself."""
    if not prompt:
        return []
    environment = (prompt.get("environment") or "").strip()
    return _searches(
        [
            *((hunt or {}).get("search_queries") or []),
            (prompt.get("context") or "").strip(),
            (prompt.get("viral_factor") or "").strip(),
            f"{environment} reel" if environment else "",
        ],
        _REEL_ENGINES,
    )


def _instagram_topic_links(hunt: dict | None) -> list[dict]:
    """`instagram.com/popular/<slug>` pages for this reel's niche. Only the
    free model produces these: a topic slug is 1-3 words someone would
    actually name the niche ("baseball-girl"), which is not something the
    analysis' prose can be sliced into."""
    return [
        {"label": f"/popular/{slug}", "url": _INSTAGRAM_TOPIC_URL.format(slug=slug)}
        for slug in ((hunt or {}).get("instagram_topics") or [])
        if slug
    ]
