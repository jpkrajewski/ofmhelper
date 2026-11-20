# integrations

- `integrations/outbound.py` — every outside `requests` call: `mount(session)`, `get`, `head`. Under OFM_FAKE_EXTERNALS a `FakeAdapter` answers by host from the fakes `features/fakes.py` `install_fakes()` registers (instagram, kie.ai, Higgsfield files, Apify, ...); any other host is refused (`ConnectionError`), never reached. `ProxyPool`: one job's proxies in turn (`start` offset), dead ones dropped, direct when none.
