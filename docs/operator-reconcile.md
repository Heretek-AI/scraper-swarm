# Operator Note — Engine Stack Reconcile (Phase 02-infra-reconcile)

## What it does

The engine stack (`scraper-swarm-{searxng,crawl4ai,valkey,egress-web}`) is
now **persistent**: swarmd keeps a desired-state file and repairs
DB-vs-Docker drift automatically, so `POST /services/test-all` stays 3/3
without manual re-apply.

Background (escalation driver):

- `file:///home/john/Projects/scraper-swarm/.roadmap/01-live-smoke/dossier.json`
  — "01-live-smoke escalated: AC1/AC2/AC5 FAIL NOW 1/3, DB running vs
  0 containers drift"
- `file:///home/john/Projects/scraper-swarm/deploy/docker-compose.control.yml`
- `file:///home/john/Projects/scraper-swarm/apps/swarmd/src/swarmd/server.py`
  — "apply_stack / get_status / service_logs; catalog cache + missing
  deploy context"
- `file:///home/john/Projects/scraper-swarm/apps/panel-api/src/panel_api/smoke_test.py`
- `file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P1-live-smoke-B1-B2`
  — "Spin up SearXNG + Crawl4AI + Smokescreen; Verify MCP Gateway query execution"

## Behavior

- **Desired state**: every `POST /services/deploy` writes
  `/var/lib/scraper-swarm/stack/wanted.json` (on the `swarm_data` volume,
  survives swarmd restarts/rebuilds). `POST /services/down` clears it so
  the loop never resurrects an intentionally stopped stack.
- **Interval**: `SWARM_RECONCILE_INTERVAL_S` (default `60`, set on the
  `swarmd` service in `deploy/docker-compose.control.yml`). First pass runs
  `SWARM_RECONCILE_BOOT_DELAY_S` (default `15`) after swarmd start, so a
  daemon/host restart recovers the stack by itself. Set the interval to `0`
  to disable the loop (on-demand repair still works).
- **Repair**: each pass diffs desired services (from the rendered
  `docker-compose.yml`) against `docker compose ps -a`.
  - `missing` / `exited` / `dead` → idempotent `up -d --remove-orphans`
    (healthy containers untouched).
  - `running` but `unhealthy` (SearXNG, Crawl4AI, Valkey healthchecks) →
    explicit `docker compose restart <service>` (`up -d` alone does not
    recycle unhealthy containers). Egress-web is distroless (no shell), so
    it has no healthcheck and is covered by State-based repair.
  - Every engine service renders with `restart: unless-stopped`, so the
    Docker daemon itself also restarts crashed containers.
- **Backoff**: after 3 consecutive failed passes the reconciler marks the
  stack `degraded` with a reason and **suspends** auto-repair instead of
  hot-looping. Re-deploy or `POST /services/reconcile` resumes it.
- **Secrets**: reconcile reports and API payloads carry service names,
  container states, and latencies only — never env/secret values.

## Where to inspect

| Question | Where |
|---|---|
| Drift right now? | `GET /services/status` → `drift`, `degraded[]` (with reason), `reconcile.last`, `repair_triggered` |
| Reconciler state (read-only)? | `GET /services/reconcile` → enabled, interval, wanted timestamp, last pass |
| Repair on demand? | `POST /services/reconcile` → report + `drift_after` (up to ~3 min) |
| Repair history? | `docker exec deploy-swarmd-1 cat /var/lib/scraper-swarm/stack/reconcile.json` |
| Live logs? | `docker logs deploy-swarmd-1` (look for `reconcile pass:`) |
| Engine containers? | `docker ps --filter name=scraper-swarm-` |
| E2E proof? | `POST /services/test-all` → 3/3 with per-service `latency_ms` |

## Scope guard

Reconciliation only: no catalog additions, no gateway/auth changes, no
swarms or sibling programmers were invoked for this phase.
