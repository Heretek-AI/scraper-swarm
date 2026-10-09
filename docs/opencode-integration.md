# OpenCode v2 Integration — Operator Note (Phase 03)

Evidence:
- `file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P2-C1-C2-C3`
- `file:///home/john/Projects/scraper-swarm/apps/panel-api/src/panel_api/routers/agents.py`
- `file:///home/john/Projects/scraper-swarm/apps/gateway/src/gateway/server.py`
- `file:///home/john/Projects/scraper-swarm/packages/opencode-plugin/src/index.ts`
- `file:///home/john/Projects/scraper-swarm/apps/panel-web/src/components/workbench/WorkbenchView.tsx`
- `file:///home/john/Projects/scraper-swarm/.roadmap/02-infra-reconcile/dossier.json`

## 1. Issue a scoped agent key (Web Console)

Workbench view → **+ Issue Key** → name (e.g. `opencode-coder-1`),
scopes `["search", "scrape"]`, rate limit (default 60 req/min), optional TTL
(`expires_in_hours`). `POST /agents/keys` returns:

- `raw_key` (`swarm_sec_…`, shown **once** — store it in your secret manager),
- `key_prefix` (display only),
- `expires_at` (ISO-8601, nullable),
- `opencode_snippet` (ready-to-paste `opencode.json` fragment).

List (`GET /agents/keys`) and audit rows expose the prefix only — never the
raw key or its hash. Unknown scopes are rejected `422` at issuance.

Equivalent API call (admin/operator session cookie required):

```bash
curl -s -b "swarm_session=$SESSION" -X POST https://<panel>/agents/keys \
  -H 'Content-Type: application/json' \
  -d '{"name":"opencode-coder-1","scopes":["search","scrape"],"expires_in_hours":720}'
```

### Service-account keys for automation (ticket #7)

Human TOTP sessions are for operators; automated clients get keys from the
host via `swarmctl` (host shell access is the authorization, consistent with
the bootstrap model — there is no network path to minting):

```bash
docker compose exec panel-api swarmctl keys create \
  --name research-svc --scopes search,scrape --expires 90d
docker compose exec panel-api swarmctl keys rotate research-svc --grace 24h
docker compose exec panel-api swarmctl keys list
```

`create` prints the raw key **once** (only the hash is stored). `rotate`
renames the old row (valid until `--grace` elapses) and mints a same-named
successor with the same scopes — zero-downtime rotation. Key creation works
without `allow_placeholder_host` once `SWARM_PUBLIC_MCP_URL` is configured
(compose passes it through; see `.env.example`). Every successful gateway
call refreshes `last_used_at` (throttled to one write per key per minute;
visible in `GET /agents/keys` and `swarmctl keys list`).

### Readiness

`GET /ready` (routed through Caddy, like `/health`) reports
`{ready, db, engines: {searxng, crawl4ai, scrapling, gpt-researcher}}` with
per-engine `{ok, ms, status}` — `ready` is true only when the DB answers and
every engine is reachable. `tools/list` filtering (#4) consumes the same
engine set via `SWARM_DEPLOYED_ENGINES` until it is wired to `/ready`.

## 2. Load `opencode.json` in OpenCode v2

Paste the returned snippet into `opencode.json`, replacing `url` with this
deployment's gateway origin (default template is `SWARM_PUBLIC_MCP_URL`,
fallback `https://swarm.example.com/mcp`):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["@scraper-swarm/opencode-plugin"],
  "mcp": {
    "scraper-swarm": {
      "type": "remote",
      "url": "https://<your-gateway>/mcp",
      "headers": { "Authorization": "Bearer swarm_sec_..." },
      "enabled": true
    }
  }
}
```

## 3. Verify the agent path

From the agent host (or the Workbench console, which POSTs the same JSON-RPC):

```bash
# tools/list — expect 200 with web_search + fetch_page
curl -s -X POST https://<gateway>/mcp \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# web_search — expect 200 with Title:/URL: lines + latency
curl -s -X POST https://<gateway>/mcp \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"web_search","arguments":{"query":"latest rust web frameworks","limit":5}}}'

# fetch_page — expect 200 with markdown
curl -s -X POST https://<gateway>/mcp \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call",
       "params":{"name":"fetch_page","arguments":{"url":"https://example.com"}}}'
```

Expected status codes:

| Case | Result |
| --- | --- |
| Valid key, in-scope tool | `200` JSON-RPC data |
| No/invalid/revoked/expired key | `401` (revoke via `DELETE /agents/keys/{id}`; expiry via `expires_in_hours`) |
| `search`-only key → `fetch_page`/`stealth_scrape` | `403` |
| Over `rate_limit_rpm` in trailing 60 s window | `429` (single-process sliding window; one HTTP call costs one hit) |

Multi-replica deployments needing a shared budget should move the bucket to
Valkey; the gateway helper (`_check_rate_limit`) is the seam.

JSON-RPC 2.0 conformance (ticket #4): malformed JSON returns `-32700`;
a missing/non-`"2.0"` envelope or non-string method returns `-32600`;
unknown methods *and* unknown tools return `-32601`; invalid tool arguments
return `-32602` with a per-field message. Requests without an `id` are
notifications: `HTTP 202` with no body. The `id` (string or number) is
echoed exactly. `initialize` negotiates: a supported client
`protocolVersion` (`2024-11-05`, `2025-03-26`, `2025-06-18`) is echoed,
otherwise the server answers its latest; `serverInfo.version` is the single
gateway package version. `tools/list` shows only tools whose scope the key
holds *and* whose engine is deployed (`SWARM_DEPLOYED_ENGINES` override;
`/ready` in #7 becomes the truth source), with `inputSchema` generated from
the same Pydantic models that validate `tools/call` arguments. Every call —
including `initialize`/`tools/list` — is authenticated and costs one
rate-limit hit (uniform-cost decision: simpler accounting, and the `429`
contract is unchanged).

Request/response bounds (ticket #6): `web_search` `limit` is clamped to
`1–20` (REST `POST /api/search` validates `422` outside that range); URLs
over `2048` characters are SSRF-denied without DNS; fetched result text is
capped at `SWARM_MAX_FETCH_BYTES` (default 3 MiB) with a
`…[truncated: showing X of Y bytes]` marker. Unresolvable hostnames and
resolver outages fail closed (`SSRF denied`) by default; operators may opt
back into Smokescreen-only delegation with
`SWARM_SSRF_RESOLVER_FAIL_CLOSED=0`. Every fetch path — including
`stealth_scrape` — runs the SSRF pre-check before any engine is contacted.
Web-facing engines reach no in-stack peer directly (`NO_PROXY` carries only
declared `direct_peers`, e.g. `gpt-researcher → searxng`); all other HTTP
traverses Smokescreen. See `docs/robots-ua-decision.md` for the
robots.txt/user-agent policy.

## 4. Fetch guard (plugin) + Workbench path

`packages/opencode-plugin` (`tool.execute.before`) **blocks** raw
`webfetch`/`fetch` with an error naming the correct swarm MCP tool — the
OpenCode v2 hook surface cannot re-route a call, so block-plus-message is the
enforced pattern (proven by `npm test` / vitest in that package). Retry2
broadens the denylist to network-exfil primitives: direct tool names
`python`/`python3`/`powershell`/`pwsh`/`cmd`/`http_request`/`socket`/`netcat`/
`nc` are blocked outright, and shell args carrying `/dev/tcp`, `socket`,
`invoke-webrequest`, `base64` pipes, or `nc` are blocked as exfil intent.
Plain `bash ls` (and `sync files`) still passes. Block messages stay generic
(tool name only, never the URL/args) so secret-bearing queries are never
echoed. Residual risk: a novel exfil binary or heavily obfuscated one-liner
the substring list cannot see still passes the editor guard — the gateway
SSRF pre-deny (`deny_reason_for_url`) + Smokescreen per-connection egress
remain the enforcement backstop. The Workbench console is the equivalent
interactive path: it calls `/mcp` directly, so every execution carries Bearer
auth, per-tool scope checks, SSRF pre-deny, and sanitized hash-chained audit
rows (`agent:<name>`).

Key issuance hardening: names are trimmed (whitespace-only `422`), unique
case-insensitively (`UNIQUE COLLATE NOCASE` + `IntegrityError → 409`, so
concurrent same-name races yield exactly one `200`), default TTL `720h`,
never-expire requires `allow_never_expire=true`, and a `REPLACE-ME`
placeholder host blocks `422` unless `allow_placeholder_host=true` is passed
(acked requests still carry the conspicuous warning). Plain-`http://`
snippet hosts warn bearer-over-cleartext.

## 5. Audit proof

`POST /security/audit/verify` (admin/operator) returns `{"valid": true,
"entries_checked": N}`; agent rows carry redacted targets (no userinfo/query)
and no bearer material. Scope/revoke/expiry/rate tests live in
`apps/gateway/tests/test_gateway.py`; issuance tests in
`apps/panel-api/tests/test_agent_keys.py`.

Denied-row flood policy (retry2 P0-3): denied calls (`401`/`403`/`429`/
`unknown_tool`) are cooldown-sampled per `(agent, action, status)` — first
per `10 s` window writes, repeats in-window are dropped (memory counter only,
no disk growth). Distinct denials still log; `25` identical bad-auth attempts
yield `1` row, not `25`. Session expiry is fail-closed (`NULL`/`''`/
whitespace/missing/unparseable/naive-past → `401`); legacy DBs missing
`sessions.expires_at` are migrated (`ADD COLUMN` + sweep `NULL`/`''` to
expired) so they never `500`. Audit scrub (`scrub_secrets_from_text`) redacts
`swarm_sec_*`, `Bearer` tokens, `token=`/`secret=` KV, and embedded-URL
query/fragments while preserving non-secret query forensics.
