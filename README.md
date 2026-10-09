# Scraper Swarm

**IUMBTEMS-led:** Scraper Swarm is a security-hardened, self-hosted search
and scraping engine cluster for AI agents. Its gateway exposes a **stable,
versioned MCP contract** (`POST /mcp`, JSON-RPC 2.0) that external research
engines — first Epistemic Swarm — depend on.

## Architecture

| Component | Role |
| --- | --- |
| `apps/gateway` | MCP gateway: auth, scopes, rate limits, SSRF pre-check, tool dispatch (`POST /mcp`, `GET /ready`, `GET /health`) |
| `apps/panel-api` | Control plane: users, TOTP sessions, agent-key issuance (`swarmctl` for service accounts), audit chain, smoke harness |
| `apps/swarmd` | Privileged sidecar: renders hardened compose from `catalog/`, reconciles drift |
| `apps/panel-web` | Operator console (Vite React), incl. the Workbench MCP console |
| `catalog/` | Service catalogue (engines + support services) |
| `deploy/` | Caddy edge proxy, control-plane compose, engine builds (Smokescreen egress proxy) |
| `contract/v1/` | Versioned golden fixtures of the MCP contract for consumers |
| `packages/opencode-plugin` | OpenCode guard: blocks raw fetch, points at the swarm MCP tools |

Engines (SearXNG, Crawl4AI, Scrapling, GPT Researcher, …) run isolated with
no internet route except via the Smokescreen egress proxy.

## Quick start

```bash
cp .env.example .env   # set SWARM_PUBLIC_MCP_URL + SWARM_DOMAIN
bash bootstrap/install.sh
```

Details: `docs/DEPLOYMENT.md`. Operator integration: `docs/opencode-integration.md`.

## MCP contract (v1)

- `POST /mcp` — strict JSON-RPC 2.0 (`-32700`/`-32600`/`-32601`/`-32602`,
  `202` notifications, `initialize` negotiation).
- Every response carries `X-Swarm-Contract: 1`; tools return text plus
  `structuredContent` with content hashes and machine-readable error codes.
- Golden fixtures: `contract/v1/fixtures/` (vendor by commit).
- Reference: `docs/opencode-integration.md` (§3–§3b); versioning rules:
  `contract/README.md`.

## Security posture

Fail-closed SSRF pre-check on every fetch path (incl. `stealth_scrape`),
minimal `NO_PROXY` (declared `direct_peers` only), resolver fail-closed by
default, input bounds + result caps, scoped keys with rotation
(`swarmctl`), hash-chained audit with secret scrubbing. Report issues per
`SECURITY.md`. Robots/user-agent policy: `docs/robots-ua-decision.md`.

## Releases

See `CHANGELOG.md`. Versions are cut as git tags by a human maintainer;
automation must never push `main` or create tags.
