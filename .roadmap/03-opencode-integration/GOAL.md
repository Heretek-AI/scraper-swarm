# Phase 03 — P2 OpenCode v2 Live Integration Demo

## Goal
Prove a real OpenCode-style agent flow end-to-end against the persistent live stack: create scoped agent key in panel → copy generated `opencode.json` MCP snippet → call `web_search` + `fetch_page` via `/mcp` with Bearer auth → audit hash-chain rows written with no secrets → fetch-guard/plugin path documented and tested.

## Context (pickup)
- 01-live-smoke waived as superseded (persistence proven by 02); 02-infra-reconcile closed conditional (reconciler live, test-all 3/3, /mcp sentinel green).
- Existing surfaces: `POST /agents/keys` (scoped `swarm_sec_*`, prefix shown, hash stored, `opencode_snippet` returned), gateway `verify_agent_token` (Bearer + `swarm_session`, per-tool scopes, 401/403), `log_agent_activity` (rw mount, sanitized, 38 rows observed), plugin `packages/opencode-plugin/src/index.ts` (tools + guard + commands), Workbench JSON-RPC console.
- Gate 2 cycle 1: brainstorm + darkharvest both FAILED (`opencode backend execution failed`) — reported per gate rule, dossier derived from repo + brain phase5 P2 (C1/C2/C3).

## Acceptance
1. Create agent key `opencode-coder-1` (or test equivalent) with scopes `["search","scrape"]` via live API; response contains one-time `raw_key`, `key_prefix`, and valid `opencode_snippet` (remote MCP URL + Authorization header template, no secret leakage beyond the one-time key).
2. Live `/mcp` `web_search("latest rust web frameworks")` (or equivalent) with `Authorization: Bearer <key>` returns HTTP 200 JSON-RPC data (titles), latency recorded; wrong scope / bad key → 401/403 as appropriate.
3. Live `/mcp` `fetch_page` on a public URL returns markdown with expected content; audit `audit_log` gains `agent:<name>` rows with hash-chain intact (`/security/audit/verify` passes) and zero secret material.
4. Fetch-guard/plugin: unit-tested proof that raw webfetch is intercepted/blocked-or-redirected to `swarm_fetch` (plugin test suite green); Workbench equivalent path documented if plugin hook cannot re-route (block + message naming correct tool).
5. Scope enforcement: `search`-only key calling `fetch_page`/`scrape` → 403; expired/revoked key → 401; rate-limit behavior documented or tested.
6. No regressions: catalog 10, pytest green, ruff clean, unauth `/mcp` 401 preserved, no secrets in logs/audit, docs note for operator (where to create key, paste snippet, test connection).

## Brief for programmer (strict)
- Implement ONLY what is needed to make the above verifiable live (key flow, snippet shape, gateway scope checks, audit wiring, plugin guard tests, Workbench docs). Cite phase hashes. No swarms, no sibling programmers.

## Exit
- Dual QA (qa-a functional, qa-b adversarial) must pass/conditional + manager tiebreak + explicit user sign-off closes phase.
