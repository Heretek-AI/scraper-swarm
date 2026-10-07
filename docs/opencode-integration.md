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

## 4. Fetch guard (plugin) + Workbench path

`packages/opencode-plugin` (`tool.execute.before`) **blocks** raw
`webfetch`/`fetch` with an error naming the correct swarm MCP tool — the
OpenCode v2 hook surface cannot re-route a call, so block-plus-message is the
enforced pattern (proven by `npm test` / vitest in that package). The
Workbench console is the equivalent interactive path: it calls `/mcp`
directly, so every execution carries Bearer auth, per-tool scope checks, SSRF
pre-deny, and sanitized hash-chained audit rows (`agent:<name>`).

## 5. Audit proof

`POST /security/audit/verify` (admin/operator) returns `{"valid": true,
"entries_checked": N}`; agent rows carry redacted targets (no userinfo/query)
and no bearer material. Scope/revoke/expiry/rate tests live in
`apps/gateway/tests/test_gateway.py`; issuance tests in
`apps/panel-api/tests/test_agent_keys.py`.
