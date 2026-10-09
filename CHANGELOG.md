# Changelog

Keep a Changelog format. The gateway package version (`apps/gateway`) is the
single source (`gateway.__version__`, enforced by test); contract versions
(`X-Swarm-Contract`) are independent — see `contract/README.md`. Releases
are cut as git tags by a human maintainer (first tag pending approval).

## [Unreleased]

### Added
- Gateway contract v1 for external clients (epic #3, `integration/gateway-v1`):
  - Strict JSON-RPC 2.0 on `/mcp`: `-32700`/`-32600`/`-32601`/`-32602`,
    `202` notifications, exact id echo, `initialize` negotiation, scope- and
    engine-filtered `tools/list` with generated schemas (#4).
  - Structured results with content hashes, `isError` error codes,
    `Retry-After` on 429, `X-Swarm-Contract: 1` (#5).
  - SSRF/egress hardening: `stealth_scrape` pre-check, minimal `NO_PROXY`
    (`direct_peers`), fail-closed resolver, input bounds, result caps,
    weekly SSRF container CI (#6).
  - Service-account keys: `swarmctl` CLI (create/rotate/list/revoke),
    `last_used_at`, deep `GET /ready` routed through Caddy, `.env.example` (#7).
  - Engine record/replay, real-tool tests, `contract/v1/fixtures/` golden
    pairs with drift gate, `ruff` + `mypy` in CI (#8).
  - Docs matching reality: README, CHANGELOG, SECURITY, contract versioning
    rules, corrected DEPLOYMENT §5 and plugin message (#9).

## [0.1.0] — pending first tag

- Gateway package version unified at `0.1.0` (was `0.0.1` vs FastAPI `0.1.0`).
