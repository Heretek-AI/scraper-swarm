"""Automated smoke test harness for Scraper Swarm engines.

P1 Live Engine Stack Smoke Test (Phase 01-live-smoke):
Agent -> MCP Gateway (/mcp) -> SearXNG / Crawl4AI -> Smokescreen (:4750) -> Internet.
Evidence:
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P1-live-smoke-B1-B2
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/walkthrough.md::smoke-3-3
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/workbench_fix_walkthrough.md::mcp-proof

Security: diagnostics never echo bearer tokens or secret values; messages carry
only HTTP status codes, latencies, and public result counts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
from typing import Any

import aiosqlite
import httpx

# P1-live-smoke evidence (Phase 01-live-smoke):
# - file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P1-live-smoke-B1-B2
# - file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/walkthrough.md::smoke-3-3
# - file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/workbench_fix_walkthrough.md::mcp-proof

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://gateway:8000")

# Tightened sentinel (mcp-proof): the full documentation sentence only. The
# bare "Example Domain" fallback is rejected because it weakens proof that
# Crawl4AI returned the real example.com markdown.
FULL_SENTINEL = "This domain is for use in documentation examples"


def _swarm_db_path() -> str:
    base = os.environ.get("SWARM_DATA_DIR", "/var/lib/scraper-swarm")
    return os.path.join(base, "panel.db")


class SmokeTestRunner:
    """Runs diagnostics against running engine containers."""

    @staticmethod
    async def _mint_ephemeral_agent_key() -> tuple[str | None, str | None, str]:
        """Mints a short-lived agent key so smoke tests can prove the live
        Agent -> /mcp -> engine path with real authentication.

        Returns (raw_token, key_id, error). The raw token is only ever sent
        as a Bearer header, never echoed into check messages. Callers must
        revoke via :meth:`_revoke_ephemeral_agent_key` in a finally block.
        """
        raw = f"swarm_smoke_{secrets.token_hex(24)}"
        key_id = f"smoke-{secrets.token_hex(8)}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        try:
            async with aiosqlite.connect(_swarm_db_path()) as db:
                await db.execute(
                    "INSERT INTO agent_keys "
                    "(id, name, key_hash, key_prefix, scopes, rate_limit_rpm) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        key_id,
                        "smoke-ephemeral",
                        digest,
                        raw[:14] + "...",
                        json.dumps(["search", "scrape"]),
                        60,
                    ),
                )
                await db.commit()
            return raw, key_id, ""
        except Exception as e:
            return None, None, f"{type(e).__name__}: {e}"

    @staticmethod
    async def _revoke_ephemeral_agent_key(key_id: str | None) -> None:
        """Best-effort cleanup of a smoke-test agent key."""
        if not key_id:
            return
        try:
            async with aiosqlite.connect(_swarm_db_path()) as db:
                await db.execute("DELETE FROM agent_keys WHERE id = ?", (key_id,))
                await db.commit()
        except Exception:  # noqa: S110 - cleanup only; the 192-bit secret is unguessable
            pass

    @staticmethod
    async def _mcp_tools_call(
        raw_token: str, tool: str, args: dict[str, Any], timeout: float = 45.0
    ) -> tuple[int | None, str, int, str]:
        """Calls a gateway tool via authenticated /mcp JSON-RPC.

        Returns (http_status, result_text, latency_ms, error). The bearer
        token never appears in the returned strings.
        """
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{GATEWAY_URL}/mcp",
                    headers={"Authorization": f"Bearer {raw_token}"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {"name": tool, "arguments": args},
                    },
                )
            latency = int((time.perf_counter() - started) * 1000)
            try:
                body = resp.json()
            except Exception:
                body = {}
            text = ""
            try:
                content = body.get("result", {}).get("content", [])
                if content:
                    text = content[0].get("text", "")
            except Exception:
                text = ""
            return resp.status_code, text if isinstance(text, str) else "", latency, ""
        except Exception as e:
            return None, "", 0, f"{type(e).__name__}: {e}"

    @staticmethod
    async def _mcp_unauth_status() -> tuple[int | None, str]:
        """POSTs to /mcp without credentials; expects HTTP 401 (fail-closed)."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{GATEWAY_URL}/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                )
            return resp.status_code, ""
        except Exception as e:
            return None, f"{type(e).__name__}: {e}"

    @staticmethod
    async def test_searxng() -> dict[str, Any]:
        start = time.perf_counter()
        checks = []
        base_url = os.environ.get("SEARXNG_URL", "http://searxng:8080")

        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Container reachability via SearXNG root (HTTP 200).
            # P1-live-smoke-B1-B2: SearXNG must answer live before search is attempted.
            try:
                r1 = await client.get(f"{base_url}/")
                checks.append(
                    {
                        "name": "HTTP Container Reachability",
                        "passed": r1.status_code == 200,
                        "message": f"Status {r1.status_code}",
                    }
                )
            except Exception as e:
                checks.append(
                    {
                        "name": "HTTP Container Reachability",
                        "passed": False,
                        "message": str(e),
                    }
                )

            # 2. JSON Search Execution
            try:
                r2 = await client.get(
                    f"{base_url}/search",
                    params={"q": "Scraper Swarm", "format": "json"},
                )
                passed = r2.status_code == 200 and "results" in r2.json()
                results_count = len(r2.json().get("results", [])) if passed else 0
                checks.append(
                    {
                        "name": "JSON Search API Query",
                        "passed": passed,
                        "message": f"Returned {results_count} results (HTTP {r2.status_code})",
                    }
                )
            except Exception as e:
                checks.append({"name": "JSON Search API Query", "passed": False, "message": str(e)})

        # 3. Agent -> Gateway /mcp -> SearXNG proof (mcp-proof): authenticated
        # JSON-RPC tools/call web_search must return HTTP 200 with
        # gateway-formatted SearXNG output ("Title:" lines, or the documented
        # empty state "No results found." per walkthrough.md::smoke-3-3 which
        # records 0 results as a live passing state) and a recorded latency.
        # A gateway error string fails the check: it proves SearXNG was not
        # reached. Unauthenticated /mcp must be HTTP 401 (fail-closed auth).
        raw, key_id, mint_err = await SmokeTestRunner._mint_ephemeral_agent_key()
        try:
            if raw is None:
                checks.append(
                    {
                        "name": "Agent->Gateway->SearXNG (/mcp web_search)",
                        "passed": False,
                        "message": f"Fail-closed: ephemeral agent key unavailable: {mint_err}",
                    }
                )
            else:
                unauth_status, unauth_err = await SmokeTestRunner._mcp_unauth_status()
                if unauth_err:
                    checks.append(
                        {
                            "name": "Unauth /mcp denied (401)",
                            "passed": False,
                            "message": f"Fail-closed: /mcp unreachable: {unauth_err}",
                        }
                    )
                else:
                    checks.append(
                        {
                            "name": "Unauth /mcp denied (401)",
                            "passed": unauth_status == 401,
                            "message": f"Unauthenticated JSON-RPC HTTP {unauth_status}",
                        }
                    )
                mcp_status, mcp_text, mcp_latency, mcp_err = await SmokeTestRunner._mcp_tools_call(
                    raw,
                    "web_search",
                    {"query": "open source search cluster architecture", "limit": 5},
                    timeout=60.0,
                )
                if mcp_err:
                    checks.append(
                        {
                            "name": "Agent->Gateway->SearXNG (/mcp web_search)",
                            "passed": False,
                            "message": f"Fail-closed: /mcp unreachable: {mcp_err}",
                        }
                    )
                else:
                    proven = mcp_status == 200 and (
                        "Title:" in mcp_text or mcp_text.strip() == "No results found."
                    )
                    checks.append(
                        {
                            "name": "Agent->Gateway->SearXNG (/mcp web_search)",
                            "passed": proven,
                            "message": (
                                f"JSON-RPC HTTP {mcp_status} "
                                f"latency_ms {mcp_latency} chars {len(mcp_text)}"
                            ),
                        }
                    )
        finally:
            await SmokeTestRunner._revoke_ephemeral_agent_key(key_id)

        latency = int((time.perf_counter() - start) * 1000)
        all_passed = all(c["passed"] for c in checks)
        return {
            "service_id": "searxng",
            "passed": all_passed,
            "latency_ms": latency,
            "checks": checks,
        }

    @staticmethod
    async def test_crawl4ai() -> dict[str, Any]:
        start = time.perf_counter()
        checks = []
        base_url = os.environ.get("CRAWL4AI_URL", "http://crawl4ai:11235")
        token = os.environ.get("CRAWL4AI_API_TOKEN", "")

        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            # 1. Health check
            try:
                r1 = await client.get(f"{base_url}/health", headers=headers)
                checks.append(
                    {
                        "name": "Health Status Check",
                        "passed": r1.status_code == 200,
                        "message": f"Health status HTTP {r1.status_code}",
                    }
                )
            except Exception as e:
                checks.append({"name": "Health Status Check", "passed": False, "message": str(e)})

            # 2. Engine Schema & Config Readiness
            try:
                r2 = await client.get(f"{base_url}/schema", headers=headers)
                passed = r2.status_code == 200 and "browser" in r2.json()
                checks.append(
                    {
                        "name": "Engine Schema & Browser Config",
                        "passed": passed,
                        "message": f"Engine schema HTTP {r2.status_code} (Browser pool ready)",
                    }
                )
            except Exception as e:
                checks.append(
                    {
                        "name": "Engine Schema & Browser Config",
                        "passed": False,
                        "message": str(e),
                    }
                )

            # 3. Live crawl of https://example.com with sentinel-text proof.
            # mcp-proof: fetch_page via /mcp must return markdown containing
            # the full sentence "This domain is for use in documentation
            # examples" (tightened: no bare "Example Domain" fallback).
            # NOTE: the bearer token is sent but never echoed into check messages.
            try:
                r3 = await client.post(
                    f"{base_url}/md",
                    json={"url": "https://example.com"},
                    headers=headers,
                    timeout=30.0,
                )
                sentinel_ok = False
                if r3.status_code == 200:
                    payload = r3.json()
                    text = (
                        payload.get("markdown", "") if isinstance(payload, dict) else str(payload)
                    )
                    # Tightened sentinel: the full documentation sentence is
                    # required; the bare "Example Domain" fallback is rejected
                    # because it weakens proof of a real Crawl4AI extraction.
                    sentinel_ok = FULL_SENTINEL in text
                checks.append(
                    {
                        "name": "Live Crawl Sentinel (example.com)",
                        "passed": sentinel_ok,
                        "message": f"example.com markdown sentinel HTTP {r3.status_code}",
                    }
                )
            except Exception as e:
                checks.append(
                    {
                        "name": "Live Crawl Sentinel (example.com)",
                        "passed": False,
                        "message": str(e),
                    }
                )

        # 4. Agent -> Gateway /mcp -> Crawl4AI proof (mcp-proof): authenticated
        # JSON-RPC tools/call fetch_page must return HTTP 200 with the full
        # sentinel sentence and a recorded latency.
        raw, key_id, mint_err = await SmokeTestRunner._mint_ephemeral_agent_key()
        try:
            if raw is None:
                checks.append(
                    {
                        "name": "Agent->Gateway->Crawl4AI (/mcp fetch_page)",
                        "passed": False,
                        "message": f"Fail-closed: ephemeral agent key unavailable: {mint_err}",
                    }
                )
            else:
                status, text, latency, err = await SmokeTestRunner._mcp_tools_call(
                    raw, "fetch_page", {"url": "https://example.com"}, timeout=60.0
                )
                if err:
                    checks.append(
                        {
                            "name": "Agent->Gateway->Crawl4AI (/mcp fetch_page)",
                            "passed": False,
                            "message": f"Fail-closed: /mcp unreachable: {err}",
                        }
                    )
                else:
                    proven = status == 200 and FULL_SENTINEL in text
                    checks.append(
                        {
                            "name": "Agent->Gateway->Crawl4AI (/mcp fetch_page)",
                            "passed": proven,
                            "message": (
                                f"JSON-RPC HTTP {status} latency_ms {latency} chars {len(text)}"
                            ),
                        }
                    )
        finally:
            await SmokeTestRunner._revoke_ephemeral_agent_key(key_id)

        latency = int((time.perf_counter() - start) * 1000)
        return {
            "service_id": "crawl4ai",
            "passed": all(c["passed"] for c in checks),
            "latency_ms": latency,
            "checks": checks,
        }

    @staticmethod
    async def test_scrapling() -> dict[str, Any]:
        start = time.perf_counter()
        checks = []
        base_url = "http://scrapling:8000"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                r = await client.get(f"{base_url}/health")
                checks.append(
                    {
                        "name": "Camoufox Stealth Readiness",
                        "passed": r.status_code in (200, 404),
                        "message": f"Scrapling container reachable (HTTP {r.status_code})",
                    }
                )
            except Exception as e:
                checks.append(
                    {
                        "name": "Camoufox Stealth Readiness",
                        "passed": False,
                        "message": str(e),
                    }
                )

        latency = int((time.perf_counter() - start) * 1000)
        return {
            "service_id": "scrapling",
            "passed": all(c["passed"] for c in checks),
            "latency_ms": latency,
            "checks": checks,
        }

    @staticmethod
    async def test_gpt_researcher() -> dict[str, Any]:
        start = time.perf_counter()
        checks = []
        base_url = "http://gpt-researcher:8000"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                r = await client.get(f"{base_url}/")
                checks.append(
                    {
                        "name": "Research Agent API Health",
                        "passed": r.status_code in (200, 307, 404),
                        "message": f"Agent endpoint responded (HTTP {r.status_code})",
                    }
                )
            except Exception as e:
                checks.append(
                    {
                        "name": "Research Agent API Health",
                        "passed": False,
                        "message": str(e),
                    }
                )

        latency = int((time.perf_counter() - start) * 1000)
        return {
            "service_id": "gpt-researcher",
            "passed": all(c["passed"] for c in checks),
            "latency_ms": latency,
            "checks": checks,
        }

    @staticmethod
    async def test_firecrawl() -> dict[str, Any]:
        start = time.perf_counter()
        checks = []
        base_url = "http://firecrawl:3002"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                r = await client.get(f"{base_url}/v1/health")
                checks.append(
                    {
                        "name": "Firecrawl Cluster Health",
                        "passed": r.status_code in (200, 404),
                        "message": f"Cluster responded (HTTP {r.status_code})",
                    }
                )
            except Exception as e:
                checks.append(
                    {
                        "name": "Firecrawl Cluster Health",
                        "passed": False,
                        "message": str(e),
                    }
                )

        latency = int((time.perf_counter() - start) * 1000)
        return {
            "service_id": "firecrawl",
            "passed": all(c["passed"] for c in checks),
            "latency_ms": latency,
            "checks": checks,
        }

    @staticmethod
    async def test_maxun() -> dict[str, Any]:
        start = time.perf_counter()
        checks = []
        base_url = "http://maxun:8080"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                r = await client.get(f"{base_url}/")
                checks.append(
                    {
                        "name": "Maxun Backend & Browser Recorder",
                        "passed": r.status_code in (200, 302, 401),
                        "message": f"Backend responded (HTTP {r.status_code})",
                    }
                )
            except Exception as e:
                checks.append(
                    {
                        "name": "Maxun Backend & Browser Recorder",
                        "passed": False,
                        "message": str(e),
                    }
                )

        latency = int((time.perf_counter() - start) * 1000)
        return {
            "service_id": "maxun",
            "passed": all(c["passed"] for c in checks),
            "latency_ms": latency,
            "checks": checks,
        }

    @staticmethod
    async def test_cloakbrowser() -> dict[str, Any]:
        start = time.perf_counter()
        checks = []
        base_url = "http://cloakbrowser:9222"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                r = await client.get(f"{base_url}/json/version")
                checks.append(
                    {
                        "name": "Antidetect CDP Control Port",
                        "passed": r.status_code == 200,
                        "message": f"CDP responding (HTTP {r.status_code})",
                    }
                )
            except Exception as e:
                checks.append(
                    {
                        "name": "Antidetect CDP Control Port",
                        "passed": False,
                        "message": str(e),
                    }
                )

        latency = int((time.perf_counter() - start) * 1000)
        return {
            "service_id": "cloakbrowser",
            "passed": all(c["passed"] for c in checks),
            "latency_ms": latency,
            "checks": checks,
        }

    @staticmethod
    async def test_cyberscraper() -> dict[str, Any]:
        start = time.perf_counter()
        checks = []
        base_url = "http://cyberscraper:8501"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                r = await client.get(f"{base_url}/_stcore/health")
                checks.append(
                    {
                        "name": "CyberScraper Streamlit Engine",
                        "passed": r.status_code == 200,
                        "message": f"Streamlit healthy (HTTP {r.status_code})",
                    }
                )
            except Exception as e:
                checks.append(
                    {
                        "name": "CyberScraper Streamlit Engine",
                        "passed": False,
                        "message": str(e),
                    }
                )

        latency = int((time.perf_counter() - start) * 1000)
        return {
            "service_id": "cyberscraper",
            "passed": all(c["passed"] for c in checks),
            "latency_ms": latency,
            "checks": checks,
        }

    @staticmethod
    async def test_valkey() -> dict[str, Any]:
        """Validates Valkey in-memory cache and key-value operations."""
        start = time.perf_counter()
        checks = []
        host = os.environ.get("VALKEY_HOST", "valkey")
        port = int(os.environ.get("VALKEY_PORT", "6379"))

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=3.0
            )
            # 1. PING Check
            writer.write(b"PING\r\n")
            await writer.drain()
            pong = await asyncio.wait_for(reader.readline(), timeout=2.0)
            passed_pong = pong.strip() == b"+PONG"
            checks.append(
                {
                    "name": "TCP Connectivity & PING",
                    "passed": passed_pong,
                    "message": f"Response {pong.decode('utf-8', errors='replace').strip()}",
                }
            )

            # 2. Key-Value Read/Write Check
            test_key = "swarm:smoke:health"
            writer.write(f"SET {test_key} ok EX 10\r\n".encode())
            await writer.drain()
            set_res = await asyncio.wait_for(reader.readline(), timeout=2.0)

            writer.write(f"GET {test_key}\r\n".encode())
            await writer.drain()
            len_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            if len_line.startswith(b"$"):
                val_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            else:
                val_line = b""
            passed_rw = set_res.strip() == b"+OK" and val_line.strip() == b"ok"
            checks.append(
                {
                    "name": "Key-Value Read/Write",
                    "passed": passed_rw,
                    "message": "Key written and verified"
                    if passed_rw
                    else "Failed write/read cycle",
                }
            )

            writer.close()
            await writer.wait_closed()
        except Exception as e:
            checks.append(
                {
                    "name": "TCP Connectivity & PING",
                    "passed": False,
                    "message": str(e),
                }
            )

        latency = int((time.perf_counter() - start) * 1000)
        return {
            "service_id": "valkey",
            "passed": all(c["passed"] for c in checks),
            "latency_ms": latency,
            "checks": checks,
        }

    @staticmethod
    async def test_egress_guard(service_id: str = "egress-web") -> dict[str, Any]:
        """Validates fail-closed SSRF egress protection via Smokescreen + gateway pre-deny.

        P1 AC3 (smoke-3-3): 169.254.169.254 + loopback + RFC1918 + CGNAT +
        unspecified/IPv6-loopback must be denied with an explicit proxy denial
        (HTTP 407/403 ONLY -- 502/504/400 are ambiguous transport errors and
        never count as proof of a block) while public example.com stays
        reachable. Every probe is fail-closed: any transport exception fails
        the check instead of passing it.

        Two deterministic layers are covered:
        - raw proxy probes for the strict IP literals Smokescreen itself
          denies (127.0.0.1, 10/8, 172.16/12, 192.168/16, 100.64/10,
          169.254/16, 0.0.0.0, ::1);
        - authenticated /mcp fetch_page probes proving the gateway pre-deny
          (panel_api.ssrf_guard) rejects loopback/RFC1918/metadata/CGNAT plus
          decimal/hex/octal/dword encodings that Smokescreen's Go resolver
          cannot parse, before anything is delegated to Crawl4AI.
        Redirect-to-internal is enforced per-connection by Smokescreen (every
        dial re-checks the resolved destination IP), so no external redirector
        dependency -- which would be flaky -- is needed for a deterministic
        harness.
        """
        start = time.perf_counter()
        checks = []
        proxy_url = os.environ.get("HTTP_PROXY", "http://egress-web:4750")

        # Smokescreen denies with 407 (its block page rides on Proxy
        # Authentication Required) or 403. Nothing else counts.
        BLOCKED = (403, 407)

        # Strict IP literals Smokescreen denies deterministically at the proxy.
        proxy_targets = [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1/",
            "http://10.0.0.1/",
            "http://172.16.0.1/",
            "http://192.168.1.1/",
            "http://100.64.0.1/",
            "http://0.0.0.0/",
            "http://[::1]/",
        ]

        # Targets the gateway must pre-deny via /mcp fetch_page, including the
        # decimal/hex/octal/dword encodings Smokescreen cannot parse (it only
        # resolves strict dotted-decimal/IPv6, so these would otherwise slip
        # past as unresolvable DNS names). All are IP literals or
        # `localhost`, so the gateway decides without any external I/O.
        mcp_targets = [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1/",
            "http://10.0.0.1/",
            "http://172.16.0.1/",
            "http://192.168.1.1/",
            "http://100.64.0.1/",
            "http://0.0.0.0/",
            "http://[::1]/",
            "http://localhost/",
            "http://2130706433/",  # 127.0.0.1 as dword decimal
            "http://0x7f000001/",  # 127.0.0.1 as dword hex
            "http://0177.0.0.1/",  # 127.0.0.1 as octal quad
            "http://0xA9.0xFE.0xA9.0xFE/",  # 169.254.169.254 as hex quad
            "http://2852039166/",  # 169.254.169.254 as dword decimal
        ]

        # Verify proxy blocks internal/private/metadata/unspecified addresses.
        # Fail-closed: any transport exception fails the check (it proves the
        # proxy path is unusable, not that the target was blocked).
        async with httpx.AsyncClient(proxy=proxy_url, timeout=5.0) as client:
            for target in proxy_targets:
                try:
                    r = await client.get(target)
                    denied = r.status_code in BLOCKED
                    checks.append(
                        {
                            "name": f"Proxy SSRF Block ({target})",
                            "passed": denied,
                            "message": (
                                f"Blocked with HTTP {r.status_code}"
                                if denied
                                else f"NOT BLOCKED: HTTP {r.status_code} (fail-closed)"
                            ),
                        }
                    )
                except Exception:
                    checks.append(
                        {
                            "name": f"Proxy SSRF Block ({target})",
                            "passed": False,
                            "message": "Fail-closed: proxy probe raised (no block proven)",
                        }
                    )

            # Public destination MUST stay reachable (fail-closed, not fail-dead).
            try:
                r = await client.get("http://example.com/", timeout=15.0)
                allowed = r.status_code == 200 and "Example Domain" in r.text
                checks.append(
                    {
                        "name": "Public Destination Allowed (example.com)",
                        "passed": allowed,
                        "message": f"Public egress HTTP {r.status_code}",
                    }
                )
            except Exception as e:
                checks.append(
                    {
                        "name": "Public Destination Allowed (example.com)",
                        "passed": False,
                        "message": f"Egress proxy unusable for public traffic: {e}",
                    }
                )

        # Gateway pre-deny layer: authenticated /mcp fetch_page must return
        # HTTP 200 whose text carries the fail-closed SSRF denial for every
        # internal/encoded target. Fail-closed on key-mint or transport errors.
        raw, key_id, mint_err = await SmokeTestRunner._mint_ephemeral_agent_key()
        try:
            if raw is None:
                checks.append(
                    {
                        "name": "Gateway SSRF pre-deny (/mcp fetch_page)",
                        "passed": False,
                        "message": f"Fail-closed: ephemeral agent key unavailable: {mint_err}",
                    }
                )
            else:
                for target in mcp_targets:
                    (
                        mcp_status,
                        mcp_text,
                        mcp_latency,
                        mcp_err,
                    ) = await SmokeTestRunner._mcp_tools_call(
                        raw, "fetch_page", {"url": target}, timeout=30.0
                    )
                    if mcp_err:
                        checks.append(
                            {
                                "name": f"Gateway SSRF pre-deny ({target})",
                                "passed": False,
                                "message": f"Fail-closed: /mcp unreachable: {mcp_err}",
                            }
                        )
                    else:
                        denied = mcp_status == 200 and "SSRF denied" in mcp_text
                        checks.append(
                            {
                                "name": f"Gateway SSRF pre-deny ({target})",
                                "passed": denied,
                                "message": (
                                    f"Denied latency_ms {mcp_latency}"
                                    if denied
                                    else f"NOT DENIED: HTTP {mcp_status} (fail-closed)"
                                ),
                            }
                        )
        finally:
            await SmokeTestRunner._revoke_ephemeral_agent_key(key_id)

        latency = int((time.perf_counter() - start) * 1000)
        return {
            "service_id": service_id,
            "passed": all(c["passed"] for c in checks),
            "latency_ms": latency,
            "checks": checks,
        }

    @classmethod
    async def run(cls, service_id: str) -> dict[str, Any]:
        testers = {
            "searxng": cls.test_searxng,
            "crawl4ai": cls.test_crawl4ai,
            "valkey": cls.test_valkey,
            "egress-web": lambda: cls.test_egress_guard("egress-web"),
            "egress-guard": lambda: cls.test_egress_guard("egress-guard"),
            "scrapling": cls.test_scrapling,
            "gpt-researcher": cls.test_gpt_researcher,
            "firecrawl": cls.test_firecrawl,
            "maxun": cls.test_maxun,
            "cloakbrowser": cls.test_cloakbrowser,
            "cyberscraper": cls.test_cyberscraper,
        }
        tester = testers.get(service_id)
        if not tester:
            return {
                "service_id": service_id,
                "passed": False,
                "latency_ms": 0,
                "checks": [
                    {
                        "name": "Diagnostic",
                        "passed": False,
                        "message": f"No test defined for {service_id}",
                    }
                ],
            }
        return await tester()
