# Contract v1 fixtures

Golden request/response pairs for the gateway's `/mcp` contract v1, for the
external consumer (Epistemic Swarm, Heretik-AI/IUMBTEMS#107) to vendor and
test its client against **without running this stack**.

## Vendoring

1. Copy this directory at a pinned commit: record the source commit hash
   next to your copy (e.g. `fixtures-from: scraper-swarm@<sha>`).
2. Match on `request` → assert `response_status`, the `response_headers`
   subset (`x-swarm-contract`, plus `retry-after` for 429s), and
   `response_body`.
3. Volatile fields are normalized: `fetched_at` is always `<fetched_at>`,
   `Retry-After` is always `<retry_after_s>` (assert presence, not value).
4. Re-vendor on every contract-version bump (see versioning rules below);
   additive v1 changes are announced on the consumer ticket.

## Regenerating

```bash
python apps/gateway/tests/gen_contract_fixtures.py
```

The generator drives the real gateway over ASGI with replayed engine
payloads (`apps/gateway/tests/engine_fixtures/`, manifest included) and
stubbed DNS — no network, fully deterministic. `test_contract_drift.py`
regenerates in memory and fails on ANY byte-level deviation, which is how
contract drift is caught: if you change `/mcp` behaviour, regenerate and
commit the updated fixtures in the same change.

## Coverage

Every tool (`web_search`, `fetch_page`, `deep_research`, `stealth_scrape`,
plus `initialize` and `tools/list` incl. scope filtering) and every produced
error code (`ssrf_denied`, `upstream_error`, `upstream_timeout`,
`engine_unavailable`, `-32602`, `-32601`, `403 scope_denied`, `429
rate_limited`). `blocked_by_policy` is reserved (no producer yet — see the
robots follow-up in `docs/robots-ua-decision.md`).

## Versioning rules

Canonical rules live in [`../README.md`](../README.md) (additive → same
version, breaking → new version + advance notice). v1 is current.
