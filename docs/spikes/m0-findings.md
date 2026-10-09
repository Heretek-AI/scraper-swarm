# M0 / Spike 0 findings

Date: 2026-10-05. Sources: upstream READMEs (raw), opencode.ai docs, `docker manifest inspect`.
Status legend: **VERIFIED** (primary source), **UNVERIFIED** (needs a runtime test in a later milestone).

## Plan-changing findings

| # | Finding | Status | Plan change |
|---|---|---|---|
| 1 | **There is no "opencode v2" release.** Latest GitHub release is `v1.18.34` (2026-09-30). The earlier "v2 / stateless MCP / OAuth 2.1 / hot reload" claims came from a secondary search summary and are unsupported. | VERIFIED | Target the **documented** surface (`mcp` block, `plugin` array, plugin hooks), which is what any v2 will most likely retain. Do not depend on v2-only features. Re-check when a v2 is published. |
| 2 | `tool.execute.before` can **mutate `output.args` or `throw`**. There is no documented way to re-route a call to another tool. | VERIFIED | The web-fetch guard **blocks with an instructive error** (naming the swarm MCP tools). It does not redirect. [2026-10-09: message corrected to the real tool names `web_search`/`fetch_page`; was recorded here with a placeholder name.] |
| 3 | `shell.env` injects env vars into **every shell execution** (AI tools and terminals). | VERIFIED | **Do not inject the gateway key via `shell.env`**: the agent could run `echo $SWARM_API_KEY` and leak it. Plugin tools read the key from the plugin process env / a `0600` config file instead. Drops one item from the plugin plan. |
| 4 | **CloakBrowser** binary needs a **license key**; the free tier needs a GitHub sign-in and allows **one concurrent session**; Pro scales concurrency. Image `cloakhq/cloakbrowser` exists. | VERIFIED | Wizard step collects the key into the vault; gateway browser-pool cap for this backend defaults to **1** on free. The consent screen states the proprietary binary + license terms. |
| 5 | **Firecrawl self-host API is unauthenticated by default**, `USE_DB_AUTHENTICATION=false` is not a full auth design, and the compose file defines **no persistent volumes**. Upstream tells you to pin an exact release tag. | VERIFIED | Firecrawl is **internal-only**; the "advanced native endpoint" toggle is **disabled for Firecrawl**. Our templates add persistent volumes. Channel defaults to exact tag, not `latest`. |
| 6 | **Smokescreen has no published image** on Docker Hub or GHCR under the names tried. It is an HTTP CONNECT proxy with `--deny-range`, `--allow-range`, `--egress-acl-file`, rate/concurrency limits. | VERIFIED | We **build it from a pinned source commit** in our own Dockerfile. Plain-HTTP (non-CONNECT) forwarding behavior is **UNVERIFIED** -> test in M1 with Chromium and httpx. |
| 7 | Scrapling's published image is **`pyd4vinci/scrapling`** (Docker Hub), not `ghcr.io/d4vinci/scrapling`. Its MCP server is an optional install feature. | VERIFIED | Catalog uses the Docker Hub name. MCP transport (stdio vs HTTP) is **UNVERIFIED** -> in M3 the gateway calls Scrapling as a library inside our own thin wrapper container if HTTP MCP is not available. |
| 8 | Crawl4AI MCP endpoints are `/mcp/sse` and `/mcp/ws`; REST on `11235`; recommends `--shm-size=1g`; supports proxies; JWT auth documented. | VERIFIED | Gateway uses the **REST API** (stable) rather than its MCP transport. |

## Image existence (`docker manifest inspect`)

OK: `searxng/searxng`, `valkey/valkey`, `unclecode/crawl4ai:latest`, `pyd4vinci/scrapling:latest`,
`cloakhq/cloakbrowser:latest`, `caddy:2`, `gptresearcher/gpt-researcher:latest`,
`getmaxun/maxun-backend:latest`, `getmaxun/maxun-frontend:latest`.
Not found: `stripe/smokescreen`, `ghcr.io/stripe/smokescreen` -> build from source.

## Still UNVERIFIED (carried forward)

- Chromium sandbox behavior under `cap_drop: ALL` + seccomp for Crawl4AI/CloakBrowser (M2).
- CyberScraper-2077 Docker path: `docker-compose.yml` is not at repo root (404); README documents `docker build`. Must be built from the repo (M5).
- GPT Researcher: use its FastAPI server directly from the gateway; `gptr-mcp` is stdio-oriented (M3).
- Maxun full compose (backend, frontend, Postgres, Redis, MinIO?) -> read docs.maxun.dev/installation/docker (M5).
- Firecrawl: pin the exact release tag and review its compose at that tag (M5).
