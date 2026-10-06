# Phase 02 — Infra Reconcile (authorized new scope)

## Goal
Make the engine stack persistent: repair DB-vs-Docker drift and auto-restart so P1 `POST /services/test-all` 3/3 is reproducible minutes later, not 15:42-only.

## Context (escalation from 01-live-smoke)
- 01-live-smoke escalated after 3 QA fails (retries 2→3→escalated). Code fixes in 8bd6e7f + 142948a + dossier 8c414f9 pushed.
- QA-A + QA-B agree: AC3/AC4/AC6 pass, but AC1/AC2/AC5 FAIL NOW — `installed_services=[searxng running, crawl4ai running]` vs `docker ps 0x scraper-swarm-*`, gateway `NXDOMAIN` for searxng/crawl4ai, `/mcp` 200 + error strings chars 83, `test-all 1/3`.
- swarmd caches catalog in memory, image lacked deploy/egress context (fixed in retry1), but no reconciliation loop / restart policy / health-driven repair.

## Acceptance
1. `GET /services/status` containers matches `installed_services` running (no drift); stale `running` auto-repaired or marked degraded with reason.
2. `scraper-swarm-{searxng,crawl4ai,valkey,egress-web}` present + healthy 10 min after deploy (restart policy / reconciler, not manual re-apply).
3. `POST /services/test-all` 3/3 reproducible on demand (searxng JSON 200 + crawl4ai sentinel + egress 407s) with latency recorded.
4. `/mcp web_search` + `fetch_page https://example.com` via live gateway return data (not error strings) with full sentinel.
5. No regressions: catalog 10, pytest green, ruff clean, compose policy-compliant, audit writes with no secrets, unauth 401 preserved.
6. Docs: operator note on reconcile behavior (interval, restart policy, where to inspect logs).

## Brief for programmer (strict)
- Implement ONLY persistence/reconciliation (swarmd reconciler, compose restart policies, status drift repair, health-driven restart). Cite phase hashes. No swarms, no sibling programmers.
- Evidence below are file:// (swarms errored Gate 1).

## Exit
- Dual QA (qa-a functional, qa-b adversarial) must pass + explicit user sign-off closes phase.
