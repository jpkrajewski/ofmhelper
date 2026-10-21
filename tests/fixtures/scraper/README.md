# Instagram answers, for the parsing tests (tests/unit/instagram/test_parsing.py)

Excerpts in Instagram's own shapes as recorded in
`src/ofmhelpers/instagram/CLAUDE.md` (verified against instagram.com
2026-10-17..20), with the surrounding noise real answers carry (fields the
client ignores, other scripts on the page) and placeholder ids/URLs. They
were NOT re-captured during the testing overhaul: a capture is a live call
through the proxies. `tests/contract/test_instagram_contract.py -m live`
checks the real site still answers this way; when it does not, capture a
fresh answer here and fix the parser against it.
