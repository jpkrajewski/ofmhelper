import pytest

from ofmhelpers.integrations.cookie_jar import CookieFileError, load_jar
from ofmhelpers.integrations.pinterest import (
    PinterestClient,
    only_pinterest,
    session_expires_at,
)


def _txt(*rows: tuple[str, str, str]) -> str:
    lines = ["# Netscape HTTP Cookie File"]
    for domain, name, value in rows:
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append(f"{domain}\t{flag}\t/\tTRUE\t1999999999\t{name}\t{value}")
    return "\n".join(lines) + "\n"


def test_only_pinterest_cookies_are_kept():
    kept = only_pinterest(
        _txt(
            (".pinterest.com", "_pinterest_sess", "s"),
            (".instagram.com", "sessionid", "x"),
            ("pl.pinterest.com", "csrftoken", "p"),
        )
    )

    assert "_pinterest_sess" in kept
    assert "csrftoken" in kept
    assert "instagram" not in kept


def test_a_locale_host_is_used_with_its_own_csrftoken():
    client = PinterestClient(
        _txt(
            (".pinterest.com", "_pinterest_sess", "s"),
            ("www.pinterest.com", "csrftoken", "w"),
            ("pl.pinterest.com", "csrftoken", "p"),
        )
    )

    sent = sorted((c.domain, c.name) for c in client._session.cookies)
    assert client._base == "https://pl.pinterest.com"
    assert client.csrf == "p"
    assert sent == [
        (".pinterest.com", "_pinterest_sess"),
        ("pl.pinterest.com", "csrftoken"),
    ]
    assert client.signed_in


def test_without_a_locale_host_www_is_used():
    client = PinterestClient(
        _txt(
            (".pinterest.com", "_pinterest_sess", "s"),
            ("www.pinterest.com", "csrftoken", "w"),
        )
    )

    assert client._base == "https://www.pinterest.com"
    assert client.csrf == "w"


def test_without_a_session_cookie_it_is_not_signed_in():
    client = PinterestClient(_txt(("www.pinterest.com", "csrftoken", "w")))

    assert not client.signed_in


def test_what_pinterest_renews_is_what_is_saved():
    client = PinterestClient(
        _txt(
            (".pinterest.com", "_pinterest_sess", "old"),
            ("www.pinterest.com", "csrftoken", "w"),
        )
    )
    client._session.cookies.set(
        "_pinterest_sess", "renewed", domain=".pinterest.com", path="/"
    )

    saved = client.cookies_txt()

    assert "renewed" in saved
    assert "\told\n" not in saved


def test_the_login_cookies_expiry_is_read():
    expires = session_expires_at(_txt((".pinterest.com", "_pinterest_sess", "s")))

    assert expires == 1999999999.0


def test_a_bad_cookies_file_never_carries_its_lines_into_a_log():
    with pytest.raises(CookieFileError) as caught:
        load_jar("not a cookies file\tsecret-value\n")

    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    assert "secret-value" not in str(caught.value)
