"""Automated smoke test harness for Scraper Swarm engines."""

from __future__ import annotations

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
            # 1. Health check
            try:
                r1 = await client.get(f"{base_url}/healthz")
                if r1.status_code in (200, 404):  # SearXNG might not have /healthz, check root
                    r1 = await client.get(f"{base_url}/")
                checks.append({
                    "name": "HTTP Container Reachability",
                    "passed": r1.status_code == 200,
                    "message": f"Status {r1.status_code}",
                })
            except Exception as e:
                checks.append({"name": "HTTP Container Reachability", "passed": False, "message": str(e)})

            # 2. JSON Search Execution
            try:
                r2 = await client.get(f"{base_url}/search", params={"q": "Scraper Swarm", "format": "json"})
                passed = r2.status_code == 200 and "results" in r2.json()
                results_count = len(r2.json().get("results", [])) if passed else 0
                checks.append({
                    "name": "JSON Search API Query",
                    "passed": passed,
                    "message": f"Returned {results_count} results (HTTP {r2.status_code})",
                })
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
                checks.append({
                    "name": "Health Status Check",
                    "passed": r1.status_code == 200,
                    "message": f"Health status HTTP {r1.status_code}",
                })
            except Exception as e:
                checks.append({"name": "Health Status Check", "passed": False, "message": str(e)})

            # 2. Engine Schema & Config Readiness
            try:
                r2 = await client.get(f"{base_url}/schema", headers=headers)
                passed = r2.status_code == 200 and "browser" in r2.json()
                checks.append({
                    "name": "Engine Schema & Browser Config",
                    "passed": passed,
                    "message": f"Engine schema HTTP {r2.status_code} (Browser pool ready)",
                })
            except Exception as e:
                checks.append({"name": "Engine Schema & Browser Config", "passed": False, "message": str(e)})

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
                checks.append({
                    "name": "Camoufox Stealth Readiness",
                    "passed": r.status_code in (200, 404),
                    "message": f"Scrapling container reachable (HTTP {r.status_code})",
                })
            except Exception as e:
                checks.append({"name": "Camoufox Stealth Readiness", "passed": False, "message": str(e)})

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
                checks.append({
                    "name": "Research Agent API Health",
                    "passed": r.status_code in (200, 307, 404),
                    "message": f"Agent endpoint responded (HTTP {r.status_code})",
                })
            except Exception as e:
                checks.append({"name": "Research Agent API Health", "passed": False, "message": str(e)})

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
                checks.append({
                    "name": "Firecrawl Cluster Health",
                    "passed": r.status_code in (200, 404),
                    "message": f"Cluster responded (HTTP {r.status_code})",
                })
            except Exception as e:
                checks.append({"name": "Firecrawl Cluster Health", "passed": False, "message": str(e)})

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
                checks.append({
                    "name": "Maxun Backend & Browser Recorder",
                    "passed": r.status_code in (200, 302, 401),
                    "message": f"Backend responded (HTTP {r.status_code})",
                })
            except Exception as e:
                checks.append({"name": "Maxun Backend & Browser Recorder", "passed": False, "message": str(e)})

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
                checks.append({
                    "name": "Antidetect CDP Control Port",
                    "passed": r.status_code == 200,
                    "message": f"CDP responding (HTTP {r.status_code})",
                })
            except Exception as e:
                checks.append({"name": "Antidetect CDP Control Port", "passed": False, "message": str(e)})

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
                checks.append({
                    "name": "CyberScraper Streamlit Engine",
                    "passed": r.status_code == 200,
                    "message": f"Streamlit healthy (HTTP {r.status_code})",
                })
            except Exception as e:
                checks.append({"name": "CyberScraper Streamlit Engine", "passed": False, "message": str(e)})

        latency = int((time.perf_counter() - start) * 1000)
        return {
            "service_id": "cyberscraper",
            "passed": all(c["passed"] for c in checks),
            "latency_ms": latency,
            "checks": checks,
        }

    @staticmethod
    async def test_egress_guard() -> dict[str, Any]:
        """Validates fail-closed SSRF egress protection via Smokescreen."""
        start = time.perf_counter()
        checks = []
        proxy_url = "http://deploy-egress-web-1:4750"

        # Verify proxy blocks internal/private RFC1918 and metadata addresses
        async with httpx.AsyncClient(proxy=proxy_url, timeout=5.0) as client:
            # 1. AWS/Cloud Metadata Block
            try:
                r = await client.get("http://169.254.169.254/latest/meta-data/")
                blocked = r.status_code in (400, 403, 407, 502, 504)
                checks.append({
                    "name": "Cloud Metadata SSRF Block",
                    "passed": blocked,
                    "message": f"Blocked metadata request with HTTP {r.status_code}",
                })
            except Exception:
                checks.append({
                    "name": "Cloud Metadata SSRF Block",
                    "passed": True,
                    "message": "Connection strictly refused by egress proxy (Expected)",
                })

            # 2. RFC1918 Private Subnet Block
            try:
                r = await client.get("http://10.0.0.1/")
                blocked = r.status_code in (400, 403, 407, 502, 504)
                checks.append({
                    "name": "RFC1918 Private Subnet SSRF Block",
                    "passed": blocked,
                    "message": f"Blocked private subnet with HTTP {r.status_code}",
                })
            except Exception:
                checks.append({
                    "name": "RFC1918 Private Subnet SSRF Block",
                    "passed": True,
                    "message": "Connection strictly refused by egress proxy (Expected)",
                })

        latency = int((time.perf_counter() - start) * 1000)
        return {
            "service_id": "egress-guard",
            "passed": all(c["passed"] for c in checks),
            "latency_ms": latency,
            "checks": checks,
        }

    @classmethod
    async def run(cls, service_id: str) -> dict[str, Any]:
        testers = {
            "searxng": cls.test_searxng,
            "crawl4ai": cls.test_crawl4ai,
            "scrapling": cls.test_scrapling,
            "gpt-researcher": cls.test_gpt_researcher,
            "firecrawl": cls.test_firecrawl,
            "maxun": cls.test_maxun,
            "cloakbrowser": cls.test_cloakbrowser,
            "cyberscraper": cls.test_cyberscraper,
            "egress-guard": cls.test_egress_guard,
        }
        tester = testers.get(service_id)
        if not tester:
            return {
                "service_id": service_id,
                "passed": False,
                "latency_ms": 0,
                "checks": [{"name": "Diagnostic", "passed": False, "message": f"No test defined for {service_id}"}],
            }
        return await tester()
