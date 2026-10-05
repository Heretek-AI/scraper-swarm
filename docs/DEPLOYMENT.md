# Scraper Swarm — Production Deployment & Operator Guide

A security-hardened, self-hosted search and scraping engine cluster for AI agents, featuring privileged sidecar orchestration, fail-closed SSRF egress isolation, multi-factor authentication, and native OpenCode v2 integration.

---

## 1. System Requirements

| Specification | Minimum | Recommended |
|---|---|---|
| **CPU** | 2 cores | 4+ cores (for parallel headless browser sessions) |
| **RAM** | 4 GB | 8 GB - 16 GB (if running heavy engines like Firecrawl) |
| **Storage** | 15 GB SSD | 40+ GB SSD |
| **Operating System** | Ubuntu 22.04+ / Debian 12 / Rocky Linux 9 / Arch Linux | Linux with kernel >= 5.15 |
| **Docker** | Docker Engine 24.0+ & Docker Compose v2 | Latest Docker CE with user in `docker` group |

---

## 2. Quickstart Installation

Run the automated bootstrap installer from the root of the repository:

```bash
git clone https://github.com/Heretek-AI/scraper-swarm.git
cd scraper-swarm
bash bootstrap/install.sh
```

The bootstrap script will:
1. Validate system prerequisites (Docker, Compose v2, available memory).
2. Create isolated data directory `/var/lib/scraper-swarm` with `0700` permissions.
3. Generate an AES-256-GCM master encryption key with `0600` permissions (`master.key`).
4. Generate an ephemeral one-time administrator bootstrap token (`swarm_boot_...`).
5. Launch the control plane stack (`caddy`, `panel-api`, `swarmd`, `gateway`, `egress-web`).

Once started, open your browser to **`http://localhost`** (or `https://<your-server-ip>`).

---

## 3. Zero-Trust Remote Access

To access your Scraper Swarm console and MCP gateway remotely without opening inbound ports 80/443 on your firewall or router:

### Option A: Cloudflare Zero-Trust Tunnel (Recommended)

1. Create a Cloudflare Tunnel in the [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/) (Networks -> Tunnels).
2. Point your public hostname (e.g. `swarm.yourdomain.com`) to `http://caddy:80`.
3. Copy your tunnel secret token.
4. Run the installer with your token:
   ```bash
   bash bootstrap/install.sh --cloudflare-token <YOUR_CLOUDFLARE_TUNNEL_TOKEN>
   ```
   Or set `CLOUDFLARE_TUNNEL_TOKEN` in `/var/lib/scraper-swarm/env/bootstrap.env` and restart:
   ```bash
   docker compose -f deploy/docker-compose.control.yml -f deploy/docker-compose.cloudflare.yml up -d
   ```

### Option B: Tailscale Mesh

If your host runs Tailscale:
```bash
tailscale serve --bg http://localhost:80
# Or enable public Tailscale Funnel:
tailscale funnel 443 on
```

---

## 4. First-Time Setup Wizard

1. **Bootstrap Token**: Paste the `swarm_boot_...` token printed by the installer into the web console.
2. **Admin Credentials**: Choose your admin username.
3. **2FA Enrollment**: Scan the generated QR code or copy the secret key into your authenticator app (Google Authenticator, Bitwarden, 1Password).
4. **Stack Selection**: Choose the search and scraping engines appropriate for your workload:
   - **Lightweight / General Purpose**: SearXNG + Crawl4AI (fast, <2 GB RAM).
   - **Stealth / Anti-Bot Bypass**: Scrapling (Camoufox browser fingerprinting).
   - **Deep Autonomous Research**: GPT Researcher (multi-source query synthesis).
   - **Enterprise Crawling**: Firecrawl (multi-container heavy queue).

---

## 5. Connecting AI Agents (OpenCode v2 & MCP)

### In the Web Console:
1. Navigate to **Agent Connections** in the side rail.
2. Click **Generate New Agent Key** (e.g. name: `researcher-agent`).
3. Select permissions: `search` and `scrape`.
4. Copy the automatically generated configuration snippet.

### Configure OpenCode v2:
Paste the generated snippet into your `~/.config/opencode/opencode.json` (or workspace `.opencode/opencode.json`):

```json
{
  "mcp": {
    "servers": {
      "scraper-swarm": {
        "url": "http://localhost:8000/mcp",
        "transport": "http",
        "headers": {
          "Authorization": "Bearer swarm_sec_YOUR_GENERATED_KEY"
        }
      }
    }
  },
  "plugins": [
    "@scraper-swarm/opencode-plugin"
  ]
}
```

The `@scraper-swarm/opencode-plugin` will:
- Intercept any unmonitored raw outbound web fetches attempted by the model.
- Halt raw socket access and instruct the model to use the authenticated `swarm_fetch` and `swarm_search` MCP tools.
- Enable the `/swarm-status` slash command directly within your OpenCode sessions.

---

## 6. Security Architecture & Threat Defense

```
                       ┌──────────────────────────────────────────────┐
                       │               EXTERNAL CLIENTS               │
                       └──────────────────────┬───────────────────────┘
                                              │ TLS (80/443) or Cloudflare Tunnel
                                              ▼
                       ┌──────────────────────────────────────────────┐
                       │             Caddy Edge Reverse Proxy         │
                       │    (Forward-Auth / HTTPS / Static SPA)       │
                       └──────────────┬───────────────────────────────┘
                                      │
                 ┌────────────────────┴────────────────────┐
                 ▼                                         ▼
   ┌───────────────────────────┐             ┌───────────────────────────┐
   │         panel-api         │             │      gateway (MCP 2.x)    │
   │  - SQLite + AES-256 Vault │             │  - JSON-RPC 2.0 / Stream  │
   │  - TOTP 2FA + RBAC        │             │  - Bearer Token Auth      │
   │  - SHA-256 Hash Chain Log │             │  - Query Audit Logging    │
   └─────────────┬─────────────┘             └─────────────┬─────────────┘
                 │ (Unix Socket: 0660)                     │ (swarm-svc internal)
                 ▼                                         ▼
   ┌───────────────────────────┐             ┌───────────────────────────┐
   │    swarmd Sidecar         │             │   Engine Containers       │
   │  - No socket in panel     │             │   SearXNG, Crawl4AI, etc. │
   │  - Compose Policy Check   │             │   (internal: true bridge) │
   │  - cap_drop: [ALL]        │             └─────────────┬─────────────┘
   └───────────────────────────┘                           │ HTTP Proxy (CONNECT)
                                                           ▼
                                             ┌───────────────────────────┐
                                             │   egress-web (Smokescreen)│
                                             │  - Fail-Closed Default    │
                                             │  - Blocks 169.254.169.254 │
                                             │  - Blocks RFC1918 subnets │
                                             └─────────────┬─────────────┘
                                                           │ Verified WAN Only
                                                           ▼
                                                      INTERNET
```

- **Docker Socket Isolation**: The web panel container never mounts `/var/run/docker.sock`. All orchestration is mediated by `swarmd` over `/var/lib/scraper-swarm/swarmd.sock` with strict intent validation.
- **Fail-Closed Egress Isolation**: Scraper engines reside in isolated Docker bridge networks (`internal: true`). They have no direct default gateway to the internet. All outbound HTTP/HTTPS must traverse Stripe Smokescreen, which terminates and drops requests to cloud metadata (`169.254.169.254`), loopback (`127.0.0.1`), and internal corporate subnets.
- **Envelope Encryption**: Secrets and session tokens are encrypted using AES-256-GCM. The master encryption key is verified on every startup for strict `0600` file permissions.
- **Tamper-Evident Audit Logging**: Every administrative mutation and agent query is recorded in an immutable SHA-256 hash-chain. The chain can be cryptographically verified at any moment from the **Security Center** tab.
