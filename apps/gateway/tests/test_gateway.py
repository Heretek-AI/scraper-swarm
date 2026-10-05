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

    monkeypatch.setattr(gw_server, "DB_PATH", str(db_file))

    transport = ASGITransport(app=gw_server.app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as client:
        yield client, raw_key

    await db.close()


@pytest.mark.asyncio
async def test_gateway_health(test_env):
    client, _ = test_env
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_gateway_auth_required(test_env):
    client, _ = test_env
    # No auth header -> 401
    r = await client.post("/mcp")
    assert r.status_code == 401

    # Invalid token -> 401
    r = await client.post("/mcp", headers={"Authorization": "Bearer bad_token"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_gateway_authenticated_mcp(test_env):
    client, raw_key = test_env
    r = await client.post("/mcp", headers={"Authorization": f"Bearer {raw_key}"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "connected"
    assert "web_search" in data["tools"]
    assert "fetch_page" in data["tools"]
