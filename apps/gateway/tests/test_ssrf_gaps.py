"""Ticket #6 regression tests: SSRF pre-check on every fetch path, caps and bounds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import gateway.server as gw_server
import pytest
from httpx import ASGITransport, AsyncClient
from panel_api import ssrf_guard
from panel_api.db import Database

# Saved before fixtures monkeypatch the module: the real tool functions.
_REAL_FETCH_PAGE = gw_server.fetch_page


@pytest.fixture
async def test_env(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "panel.db"
    db = Database(db_file)
    await db.connect()

    raw_key = "swarm_sec_testtoken12345"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    await db.conn.execute(
        """
        INSERT INTO agent_keys (id, name, key_hash, key_prefix, scopes, rate_limit_rpm)
        VALUES ('k1', 'agent-test', ?, 'swarm_sec_test...', ?, 60)
        """,
        (key_hash, json.dumps(["search", "scrape"])),
    )
    await db.conn.commit()

    async def mock_search(query: str, limit: int = 5):
        return f"Mock search result for {query}"

    async def mock_fetch(url: str):
        return f"# Mock Markdown for {url}"

    monkeypatch.setattr(gw_server, "DB_PATH", str(db_file))
    monkeypatch.setattr(gw_server, "web_search", mock_search)
    monkeypatch.setattr(gw_server, "fetch_page", mock_fetch)
    gw_server._reset_rate_limits()

    transport = ASGITransport(app=gw_server.app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as client:
        yield client, raw_key, db

    gw_server._reset_rate_limits()
    await db.close()


def _mcp_call(method: str, params: dict | None = None, rpc_id: int = 1) -> dict:
    body: dict = {"jsonrpc": "2.0", "method": method, "id": rpc_id}
    if params is not None:
        body["params"] = params
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1/",
        "http://10.0.0.1:6379",
        "http://crawl4ai:11235",
    ],
)
async def test_stealth_scrape_internal_url_denied_no_egress(test_env, monkeypatch, url):
    """#6: stealth_scrape runs the SSRF pre-check; internal/peer URLs never reach Scrapling."""
    client, raw_key, _ = test_env
    monkeypatch.setattr(ssrf_guard, "resolve_host", lambda host, timeout=3.0: ["10.9.9.9"])

    egress_attempts: list = []

    class _NoEgressClient:
        def __init__(self, *a, **k):
            egress_attempts.append((a, k))

        async def __aenter__(self):
            raise AssertionError(f"egress attempted for denied URL {url}")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(gw_server.httpx, "AsyncClient", _NoEgressClient)
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_mcp_call("tools/call", {"name": "stealth_scrape", "arguments": {"url": url}}, 61),
    )
    assert r.status_code == 200
    text = r.json()["result"]["content"][0]["text"]
    assert "SSRF denied" in text
    assert egress_attempts == []


@pytest.mark.asyncio
async def test_fetch_page_resolver_timeout_fails_closed(test_env, monkeypatch):
    """#6: a resolver timeout on fetch_page fails closed (no delegation to the engine)."""
    client, raw_key, _ = test_env

    async def _real_fetch(url: str):
        return await _REAL_FETCH_PAGE(url)

    monkeypatch.setattr(gw_server, "fetch_page", _real_fetch)

    def _boom(_host, timeout=3.0):
        raise TimeoutError("dns timed out")

    monkeypatch.setattr(ssrf_guard, "resolve_host", _boom)
    monkeypatch.delenv("SWARM_SSRF_RESOLVER_FAIL_CLOSED", raising=False)
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_mcp_call(
            "tools/call",
            {"name": "fetch_page", "arguments": {"url": "https://example.com/x"}},
            62,
        ),
    )
    assert r.status_code == 200
    assert "SSRF denied" in r.json()["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_web_search_limit_bounded(test_env, monkeypatch):
    """#6: limit is bounded to 1..20 before the engine is hit."""
    client, raw_key, _ = test_env
    seen: list[int] = []

    async def _capture(query: str, limit: int = 5):
        seen.append(limit)
        return "ok"

    monkeypatch.setattr(gw_server, "web_search", _capture)
    headers = {"Authorization": f"Bearer {raw_key}"}
    await client.post(
        "/mcp",
        headers=headers,
        json=_mcp_call(
            "tools/call", {"name": "web_search", "arguments": {"query": "q", "limit": 500}}, 63
        ),
    )
    await client.post(
        "/mcp",
        headers=headers,
        json=_mcp_call(
            "tools/call", {"name": "web_search", "arguments": {"query": "q", "limit": 0}}, 64
        ),
    )
    assert seen == [20, 1]


@pytest.mark.asyncio
async def test_fetch_page_url_length_bound(test_env):
    """#6: over-long URLs are denied without engine contact."""
    client, raw_key, _ = test_env
    long_url = "https://example.com/" + "a" * 3000
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_mcp_call("tools/call", {"name": "fetch_page", "arguments": {"url": long_url}}, 65),
    )
    assert r.status_code == 200
    assert "SSRF denied" in r.json()["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_fetch_result_size_cap_truncates(test_env, monkeypatch):
    """#6: fetched bytes are capped (configurable) and marked truncated."""
    client, raw_key, _ = test_env
    monkeypatch.setattr(gw_server, "MAX_FETCH_BYTES", 64)

    async def _big(url: str):
        return "X" * 1000

    monkeypatch.setattr(gw_server, "fetch_page", _big)
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_mcp_call(
            "tools/call",
            {"name": "fetch_page", "arguments": {"url": "https://example.com/"}},
            66,
        ),
    )
    assert r.status_code == 200
    text = r.json()["result"]["content"][0]["text"]
    assert len(text) <= 64 + 128
    assert "truncated" in text.lower()
