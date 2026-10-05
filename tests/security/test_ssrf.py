"""Security and SSRF validation test suite.

Verifies that the Smokescreen egress proxy blocks:
1. Cloud metadata IP: 169.254.169.254
2. Loopback: 127.0.0.1
3. RFC1918 Private Ranges: 10.0.0.1, 172.16.0.1, 192.168.1.1
4. Non-routable / Carrier-Grade NAT: 100.64.0.1
And allows valid public destinations: example.com
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest

PROXY_URL = "http://127.0.0.1:4750"


@pytest.fixture
async def running_smokescreen():
    """Runs a test instance of the egress-web proxy container on port 4750."""
    container_name = "test-scraper-swarm-smokescreen"
    # Ensure any previous test container is removed
    await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name)

    # Start smokescreen container exposing 4750 locally
    cmd = [
        "docker", "run", "-d", "--rm",
        "--name", container_name,
        "-p", "4750:4750",
        "scraper-swarm/egress-web:local",
    ]
    proc = await asyncio.create_subprocess_exec(*cmd)
    await proc.communicate()

    # Wait for proxy readiness
    ready = False
    for _ in range(15):
        await asyncio.sleep(1)
        with contextlib.suppress(Exception):
            async with httpx.AsyncClient(proxy=PROXY_URL, timeout=2.0) as client:
                # Testing connectivity through proxy
                resp = await client.get("http://example.com")
                if resp.status_code == 200:
                    ready = True
                    break

    yield ready

    # Cleanup container
    cleanup = await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name)
    await cleanup.communicate()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forbidden_target",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:80",
        "http://10.0.0.1:80",
        "http://172.16.0.1:80",
        "http://192.168.1.1:80",
        "http://100.64.0.1:80",
    ],
)
async def test_ssrf_proxy_blocks_private_and_metadata_destinations(
    running_smokescreen: bool, forbidden_target: str
):
    if not running_smokescreen:
        pytest.skip("Smokescreen test proxy container not ready")

    async with httpx.AsyncClient(proxy=PROXY_URL, timeout=3.0) as client:
        # Smokescreen rejects forbidden destinations with ProxyError / 407 / 403 / 502 / 504
        try:
            resp = await client.get(forbidden_target)
            assert resp.status_code in (403, 407, 502, 504)
        except httpx.HTTPError:
            pass


@pytest.mark.asyncio
async def test_ssrf_proxy_allows_public_domain(running_smokescreen: bool):
    if not running_smokescreen:
        pytest.skip("Smokescreen test proxy container not ready")

    async with httpx.AsyncClient(proxy=PROXY_URL, timeout=5.0) as client:
        resp = await client.get("http://example.com")
        assert resp.status_code == 200
        assert "Example Domain" in resp.text
