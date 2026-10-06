# Operator Note — Engine Stack Reconcile (Phase 02-infra-reconcile, retry1)

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
- `file:///home/john/Projects/scraper-swarm/.roadmap/02-infra-reconcile/dossier.json`

## Behavior

- **Desired state**: every `POST /services/deploy` writes
  `/var/lib/scraper-swarm/stack/wanted.json` (on the `swarm_data` volume,
  survives swarmd restarts/rebuilds, `0600`). `POST /services/down` clears
  it so the loop never resurrects an intentionally stopped stack. A corrupt
  `wanted.json` is **degraded** with an alert-ready reason (`wanted.json
  corrupt ...; re-deploy to restore`) — never fail-open clean.
- **Interval**: `SWARM_RECONCILE_INTERVAL_S` (default `60`, set on the
  `swarmd` service in `deploy/docker-compose.control.yml`). First pass runs
  `SWARM_RECONCILE_BOOT_DELAY_S` (default `15`) after swarmd start, so a
  daemon/host restart recovers the stack by itself. Set the interval to `0`
  to disable **only the periodic loop** — status-triggered background repair
  (`GET /services/status` with drift, max 1 per 120s cooldown) and on-demand
  `POST /services/reconcile` still fire. There is no single flag that
  disables all repair; stopping repair entirely requires stopping the stack
  (`POST /services/down` clears desired state).
- **Repair**: each pass diffs desired services (from the rendered
  `docker-compose.yml`) against `docker compose ps -a`.
  - `missing` / `exited` / `dead` → idempotent `up -d --remove-orphans`
    (healthy containers untouched).
  - `running` but `unhealthy` (SearXNG, Crawl4AI, Valkey healthchecks) →
    explicit `docker compose restart <service>` (`up -d` alone does not
    recycle unhealthy containers). Egress-web is distroless (no shell), so
    it has no healthcheck (`Health == ""`) and is covered by State-based
    repair (empty health counts as healthy when `State == running`).
  - `running` but `starting` (healthcheck still starting) is
    **degraded/pending, never healthy**: it is left to finish starting (no
    forced restart), stays in `still_missing`/`drift` with reason
    `starting`, and is never counted in `repaired` until `healthy`/running.
    Panel `GET /services/status` and swarmd agree on this (same predicate).
  - An empty/missing/unparseable rendered `docker-compose.yml` (or zero
    services) is **degraded** with a reason (`...; re-deploy to restore`),
    preserves the suspend counter, and triggers no clean reset — it never
    masks drift as healthy.
  - Every engine service renders with `restart: unless-stopped`, so the
    Docker daemon itself also restarts crashed containers.
- **Backoff / suspend / resume**: after 3 consecutive failed passes the
  reconciler marks the stack `degraded` with a reason and **suspends**
  auto-repair instead of hot-looping. Two resume paths (both audited):
  1. Re-deploy (`POST /services/deploy` → `apply_stack`) resets the backoff
     counter to 0 (`backoff_reset_by: re-deploy`), so the next loop pass
     repairs; 2. `POST /services/reconcile` sends `force=true` for **one**
     resume repair attempt even while suspended (report carries
     `forced: true` + `resume_note`; audit `reconcile_now` records
     `forced`). A successful forced verify resets the counter; a failed one
     increments it.
- **History**: `reconcile.json` remains the latest pass, plus append-only
  `reconcile-history.jsonl` (last 50 passes, `0600`) so a single overwrite
  cannot lose the repair trail. `GET /services/reconcile` returns
  `history_tail` (last 5) + `history_len`.
- **Logs hardening**: `GET /services/{id}/logs?lines=N` requires `N` in
  `1..1000` (400 otherwise, checked in panel-api and swarmd) and an
  allowlisted service name; raw docker stderr is logged server-side and a
  generic `log fetch failed` / `Failed to fetch logs` is returned (no
  error-text oracle). `POST /services/{id}/restart` wires the
  `restart_service` intent (admin/operator auth, allowlisted name); `GET
  /services/status` and post-reconcile verify use `get_ps_all` (fallback to
  `get_ps`), so no dead 0660-socket surface remains.
- **Latency**: `POST /services/test-all` records per-service `latency_ms`
  on every result (including `/mcp` hop latencies inside check messages).
  No pass/fail latency threshold is enforced — latency is evidence, not a
  gate. When any container is `Health=starting`, the response includes
  `starting: [...]` + `starting_note` explicitly instead of counting it
  healthy.
- **Secrets**: reconcile reports and API payloads carry service names,
  container states, and latencies only — never env/secret values.

## Where to inspect

| Question | Where |
|---|---|
| Drift right now? | `GET /services/status` → `drift`, `degraded[]` (with reason incl. `starting`/`unhealthy`), `reconcile.last`, `repair_triggered` |
| Reconciler state (read-only)? | `GET /services/reconcile` → enabled, interval, wanted timestamp, `wanted_corrupt`, last pass, `history_tail` |
| Repair on demand (incl. suspended resume)? | `POST /services/reconcile` → report (`forced`/`resume_note` when resuming) + `drift_after` (up to ~3 min) |
| Repair history? | `docker exec deploy-swarmd-1 cat /var/lib/scraper-swarm/stack/reconcile.json` (latest) and `reconcile-history.jsonl` (last 50) |
| Live logs? | `docker logs deploy-swarmd-1` (look for `reconcile pass:`) |
| Engine containers? | `docker ps --filter name=scraper-swarm-` |
| E2E proof? | `POST /services/test-all` → 3/3 with per-service `latency_ms` (+ `starting` when pending) |

## Scope guard

Reconciliation only: no catalog additions, no gateway/auth changes, no
swarms or sibling programmers were invoked for this phase.
