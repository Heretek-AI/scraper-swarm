"""Tests for MCP Gateway authentication and tool dispatch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

import gateway.server as gw_server
from panel_api.db import Database


@pytest.fixture
async def test_env(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "panel.db"
    db = Database(db_file)
    await db.connect()

    # Pre-seed an agent key
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

    transport = ASGITransport(app=gw_server.app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as client:
        yield client, raw_key, db

    await db.close()


@pytest.mark.asyncio
async def test_gateway_health(test_env):
    client, *_ = test_env
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_gateway_auth_required(test_env):
    client, *_ = test_env
    # No auth header -> 401
    r = await client.post("/mcp")
    assert r.status_code == 401

    # Invalid token -> 401
    r = await client.post("/mcp", headers={"Authorization": "Bearer bad_token"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_gateway_authenticated_mcp(test_env):
    client, raw_key, _ = test_env
    r = await client.post("/mcp", headers={"Authorization": f"Bearer {raw_key}"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "connected"
    assert "web_search" in data["tools"]
    assert "fetch_page" in data["tools"]


@pytest.mark.asyncio
async def test_gateway_jsonrpc_protocol(test_env):
    client, raw_key, _ = test_env
    headers = {"Authorization": f"Bearer {raw_key}"}

    # 1. Initialize
    r = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
    assert r.status_code == 200
    init_res = r.json()
    assert init_res["result"]["serverInfo"]["name"] == "ScraperSwarmGateway"

    # 2. Tools list
    r = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "tools/list", "id": 2})
    assert r.status_code == 200
    tools_res = r.json()
    tool_names = [t["name"] for t in tools_res["result"]["tools"]]
    assert "web_search" in tool_names
    assert "fetch_page" in tool_names

    # 3. Call tool
    r = await client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "web_search", "arguments": {"query": "python"}}, "id": 3},
    )
    assert r.status_code == 200
    call_res = r.json()
    assert "result" in call_res
    assert len(call_res["result"]["content"]) > 0


@pytest.mark.asyncio
async def test_gateway_rest_api_and_audit(test_env):
    client, raw_key, db = test_env
    headers = {"Authorization": f"Bearer {raw_key}"}

    r = await client.post("/api/search", headers=headers, json={"query": "test query", "limit": 2})
    assert r.status_code == 200
    assert "query" in r.json()

    r = await client.post("/api/fetch", headers=headers, json={"url": "https://example.com"})
    assert r.status_code == 200
    assert "url" in r.json()

    # Verify audit logs were written into SQLite
    async with db.conn.execute("SELECT actor, action, target FROM audit_log ORDER BY id ASC") as cur:
        rows = await cur.fetchall()
        assert len(rows) >= 2
        actions = [row["action"] for row in rows]
        assert "web_search" in actions
        assert "fetch_page" in actions

