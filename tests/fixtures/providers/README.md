# Provider responses

One body per endpoint the clients read, in the providers' wire format
(kie.ai: HTTP 200 with the outcome in `code`; Higgsfield: the SDK's JSON).

Origin: the shapes documented at docs.kie.ai / docs.higgsfield.ai and
verified against the live APIs when the clients were written (see
`src/ofmhelpers/aigenproviders/CLAUDE.md`), with ids and URLs replaced by
placeholders. They were NOT re-recorded during the testing overhaul: that
needs paid calls. `tests/contract/test_ai_provider_contract.py -m live`
checks the real APIs still answer this way.
