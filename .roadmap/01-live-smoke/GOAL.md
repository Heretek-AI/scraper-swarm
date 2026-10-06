# Phase 01 — P1 Live Engine Stack Smoke Test

## Goal
Verify the live path `Agent -> MCP Gateway (/mcp JSON-RPC 2.0) -> SearXNG / Crawl4AI -> Smokescreen egress (:4750) -> Internet` end-to-end, with fail-closed SSRF proof, from the Brain d3380741 checkpoint.

## Context (pickup)
- Control plane live: `deploy/docker-compose.control.yml` (panel-api, swarmd, gateway, caddy, egress-web) with `:ro,z` catalog mounts (e8da54b).
- Workbench dual-auth fixed (2a0bdfb): Bearer + `swarm_session` cookie, JSON-RPC 2.0 to `/mcp`, `CRAWL4AI_ALLOW_INTERNAL_URLS=true` delegating SSRF to Smokescreen.
- Prior proof: walkthrough smoke JSON 3/3 (searxng 208ms / crawl4ai 99ms / egress 26ms), 54/54 pytest, Vite clean.
- Swarm cycle 1/5: brainstorm + darkharvest FAILED (`opencode backend execution failed`) — reported per gate rule, frontier derived from `scraper_swarm_phase5_roadmap.md` P1.

## Acceptance
1. `web_search` via live `/mcp` returns SearXNG JSON (HTTP 200) with latency recorded.
2. `fetch_page` via live `/mcp` returns Crawl4AI markdown for `https://example.com` (contains expected sentinel text).
3. Smokescreen blocks `169.254.169.254` and RFC1918 (`10.0.0.1`, `192.168.1.1`) with 407/403; public `example.com` allowed.
4. `swarmd` rendered compose for the live stack is policy-compliant (non-root, `cap_drop:[ALL]`, internal networks, no Docker socket leak).
5. `POST /services/test-all` (or equivalent smoke runner) reports 3/3 pass; no secrets in logs/audit.
6. No implementation drift: catalog still 10 entries; existing pytest suite stays green (or deltas explained).

## Brief for programmer (strict)
- Implement ONLY what is needed to make the above verifiable live (fix diagnostics, gateway routing, compose rendering, or test harness). Cite phase hashes in code comments/commit msg.
- Do NOT invoke swarms or other programmers. May call `iumbtems_verify_quote` for quote checks.
- Evidence pointers below are `file://` (no VERIFIED swarm hashes — swarms errored this gate).

## Exit
- Dual QA (qa-a functional, qa-b adversarial) must both `pass` (or manager tiebreak + user sign-off).
- Explicit user sign-off closes this phase before P2 starts.
