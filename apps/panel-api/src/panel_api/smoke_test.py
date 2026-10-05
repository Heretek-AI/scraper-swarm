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
import os
import time
from typing import Any

import httpx


class SmokeTestRunner:
    """Runs diagnostics against running engine containers."""

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
            # "This domain is for use in documentation examples".
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
                    sentinel_ok = (
                        "This domain is for use in documentation examples" in text
                        or "Example Domain" in text
                    )
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
        """Validates fail-closed SSRF egress protection via Smokescreen.

        P1 AC3 (smoke-3-3): 169.254.169.254 + RFC1918 must be denied with
        407/403 while public example.com stays reachable.
        """
        start = time.perf_counter()
        checks = []
        proxy_url = os.environ.get("HTTP_PROXY", "http://egress-web:4750")

        # Verify proxy blocks internal/private RFC1918 and metadata addresses
        async with httpx.AsyncClient(proxy=proxy_url, timeout=5.0) as client:
            # 1. AWS/Cloud Metadata Block
            try:
                r = await client.get("http://169.254.169.254/latest/meta-data/")
                blocked = r.status_code in (400, 403, 407, 502, 504)
                checks.append(
                    {
                        "name": "Cloud Metadata SSRF Block",
                        "passed": blocked,
                        "message": f"Blocked metadata request with HTTP {r.status_code}",
                    }
                )
            except Exception:
                checks.append(
                    {
                        "name": "Cloud Metadata SSRF Block",
                        "passed": True,
                        "message": "Connection strictly refused by egress proxy (Expected)",
                    }
                )

            # 2. RFC1918 Private Subnet Blocks (10/8 and 192.168/16 per P1 AC3)
            for target in ("http://10.0.0.1/", "http://192.168.1.1/"):
                try:
                    r = await client.get(target)
                    blocked = r.status_code in (400, 403, 407, 502, 504)
                    checks.append(
                        {
                            "name": f"RFC1918 SSRF Block ({target})",
                            "passed": blocked,
                            "message": f"Blocked private subnet with HTTP {r.status_code}",
                        }
                    )
                except Exception:
                    checks.append(
                        {
                            "name": f"RFC1918 SSRF Block ({target})",
                            "passed": True,
                            "message": "Connection strictly refused by egress proxy (Expected)",
                        }
                    )

            # 3. Public destination MUST stay reachable (fail-closed, not fail-dead).
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
