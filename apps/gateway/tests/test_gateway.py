"""Tests for MCP Gateway authentication and tool dispatch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import gateway.server as gw_server
import pytest
from httpx import ASGITransport, AsyncClient
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
    gw_server._reset_rate_limits()

    transport = ASGITransport(app=gw_server.app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as client:
        yield client, raw_key, db

    gw_server._reset_rate_limits()
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
    r = await client.post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "initialize", "id": 1}
    )
    assert r.status_code == 200
    init_res = r.json()
    assert init_res["result"]["serverInfo"]["name"] == "ScraperSwarmGateway"

    # 2. Tools list
    r = await client.post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "tools/list", "id": 2}
    )
    assert r.status_code == 200
    tools_res = r.json()
    tool_names = [t["name"] for t in tools_res["result"]["tools"]]
    assert "web_search" in tool_names
    assert "fetch_page" in tool_names

    # 3. Call tool
    r = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "web_search", "arguments": {"query": "python"}},
            "id": 3,
        },
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
    async with db.conn.execute(
        "SELECT actor, action, target FROM audit_log ORDER BY id ASC"
    ) as cur:
        rows = await cur.fetchall()
        assert len(rows) >= 2
        actions = [row["action"] for row in rows]
        assert "web_search" in actions
        assert "fetch_page" in actions


@pytest.mark.asyncio
async def test_gateway_session_cookie_auth(test_env):
    client, _, db = test_env

    # 1. Seed an admin user and session into DB
    admin_session_token = "sess_admin_test_token_123"
    await db.conn.execute(
        "INSERT INTO users (id, username, role) VALUES ('u-admin-1', 'admin_tester', 'admin')"
    )
    await db.conn.execute(
        """
        INSERT INTO sessions (token, user_id, username, role)
        VALUES (?, 'u-admin-1', 'admin_tester', 'admin')
        """,
        (admin_session_token,),
    )
    await db.conn.commit()

    # Call /mcp with swarm_session cookie
    cookies = {"swarm_session": admin_session_token}
    r = await client.post(
        "/mcp",
        cookies=cookies,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "web_search", "arguments": {"query": "cookie test"}},
            "id": 10,
        },
    )
    assert r.status_code == 200
    res = r.json()
    assert "result" in res
    assert res["result"]["content"][0]["text"] == "Mock search result for cookie test"

    # Also test fetch_page with session cookie
    r = await client.post(
        "/mcp",
        cookies=cookies,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "fetch_page", "arguments": {"url": "https://example.com"}},
            "id": 11,
        },
    )
    assert r.status_code == 200
    assert "result" in r.json()

    # 2. Test viewer role: permitted for search, forbidden for scrape
    viewer_session_token = "sess_viewer_test_token_456"
    await db.conn.execute(
        "INSERT INTO users (id, username, role) VALUES ('u-viewer-1', 'viewer_user', 'viewer')"
    )
    await db.conn.execute(
        """
        INSERT INTO sessions (token, user_id, username, role)
        VALUES (?, 'u-viewer-1', 'viewer_user', 'viewer')
        """,
        (viewer_session_token,),
    )
    await db.conn.commit()

    # Search should succeed for viewer
    r = await client.post(
        "/mcp",
        cookies={"swarm_session": viewer_session_token},
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "web_search", "arguments": {"query": "viewer query"}},
            "id": 12,
        },
    )
    assert r.status_code == 200

    # Scrape should fail with 403 Forbidden for viewer
    r = await client.post(
        "/mcp",
        cookies={"swarm_session": viewer_session_token},
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "fetch_page", "arguments": {"url": "https://example.com"}},
            "id": 13,
        },
    )
    assert r.status_code == 403


# Phase 03-opencode-integration (P2 OpenCode v2 live integration):
# - file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P2-C1-C2-C3
# - file:///home/john/Projects/scraper-swarm/apps/panel-api/src/panel_api/routers/agents.py
# - file:///home/john/Projects/scraper-swarm/apps/gateway/src/gateway/server.py
# Scope enforcement 403, revoked/expired 401, rate-limit 429, audit zero-secrets.


async def _seed_agent_key(db, key_id, raw, scopes, rpm=60, expires_at=None):
    key_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    await db.conn.execute(
        """
        INSERT INTO agent_keys (id, name, key_hash, key_prefix, scopes, rate_limit_rpm, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key_id,
            f"agent-{key_id}",
            key_hash,
            raw[:14] + "...",
            json.dumps(scopes),
            rpm,
            expires_at,
        ),
    )
    await db.conn.commit()


@pytest.mark.asyncio
async def test_gateway_search_only_key_denied_scrape_403(test_env):
    """AC5: a search-scoped Bearer key calling fetch_page/stealth_scrape gets 403."""
    client, _, db = test_env
    raw = "swarm_sec_searchonly00001"
    await _seed_agent_key(db, "k-search-only", raw, ["search"])
    headers = {"Authorization": f"Bearer {raw}"}

    r = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "fetch_page", "arguments": {"url": "https://example.com"}},
            "id": 21,
        },
    )
    assert r.status_code == 403

    r = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "stealth_scrape", "arguments": {"url": "https://example.com"}},
            "id": 22,
        },
    )
    assert r.status_code == 403

    # web_search with the same key still succeeds (scope allows it).
    r = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "web_search", "arguments": {"query": "scoped ok"}},
            "id": 23,
        },
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_gateway_revoked_key_401(test_env):
    """AC5: deleting the key row (revocation) turns subsequent calls into 401."""
    client, _, db = test_env
    raw = "swarm_sec_revoke00000001"
    await _seed_agent_key(db, "k-revoke", raw, ["search", "scrape"])
    headers = {"Authorization": f"Bearer {raw}"}

    r = await client.post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "tools/list", "id": 31}
    )
    assert r.status_code == 200

    await db.conn.execute("DELETE FROM agent_keys WHERE id = ?", ("k-revoke",))
    await db.conn.commit()

    r = await client.post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "tools/list", "id": 32}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_gateway_expired_key_401(test_env):
    """AC5: a past-expires_at key fails closed with 401; a future one passes."""
    client, _, db = test_env
    expired_raw = "swarm_sec_expired0000001"
    await _seed_agent_key(
        db, "k-expired", expired_raw, ["search", "scrape"], expires_at="2000-01-01T00:00:00+00:00"
    )
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {expired_raw}"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 41},
    )
    assert r.status_code == 401

    fresh_raw = "swarm_sec_fresh000000001"
    await _seed_agent_key(
        db, "k-fresh", fresh_raw, ["search"], expires_at="2099-01-01T00:00:00+00:00"
    )
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {fresh_raw}"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 42},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_gateway_rate_limit_429(test_env):
    """AC5: keys over their rpm budget get HTTP 429 (documented sliding 60s window)."""
    client, _, db = test_env
    raw = "swarm_sec_ratelimit00001"
    await _seed_agent_key(db, "k-ratelimit", raw, ["search"], rpm=2)
    headers = {"Authorization": f"Bearer {raw}"}
    body = {"jsonrpc": "2.0", "method": "tools/list", "id": 51}

    assert (await client.post("/mcp", headers=headers, json=body)).status_code == 200
    assert (await client.post("/mcp", headers=headers, json=body)).status_code == 200
    r = await client.post("/mcp", headers=headers, json=body)
    assert r.status_code == 429


@pytest.mark.asyncio
async def test_gateway_audit_rows_carry_no_secrets(test_env):
    """AC3/AC6: agent audit rows are written with actor agent:<name> and contain
    no bearer material (raw key, hash, or Authorization header)."""
    client, raw_key, db = test_env
    headers = {"Authorization": f"Bearer {raw_key}"}
    await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "web_search", "arguments": {"query": "secret probe"}},
            "id": 61,
        },
    )
    await client.post(
        "/api/fetch", headers=headers, json={"url": "https://example.com/?tok=abc&x=1#frag"}
    )

    async with db.conn.execute(
        "SELECT actor, action, target, details, entry_hash, prev_hash FROM audit_log"
    ) as cur:
        rows = await cur.fetchall()
    assert len(rows) >= 2
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    for row in rows:
        blob = f"{row['actor']} {row['action']} {row['target']} {row['details']}"
        assert raw_key not in blob
        assert key_hash not in blob
        assert "Bearer" not in blob
    assert any(str(r["actor"]).startswith("agent:") for r in rows)


# Phase 03 retry1 QA-B P0-1/P0-2/P0-3: session expiry 401, session 429, audit
# query preserved + denied rows. Evidence hashes in module docstring above.

@pytest.mark.asyncio
async def test_gateway_expired_session_401(test_env):
    """Retry1 P0-1: expired swarm_session (sess-expired) fails closed with 401."""
    client, _, db = test_env
    await db.conn.execute(
        "INSERT INTO users (id, username, role) VALUES ('u-exp-1', 'exp_user', 'admin')"
    )
    await db.conn.execute(
        "INSERT INTO sessions (token, user_id, username, role, expires_at)"
        " VALUES ('sess-expired', 'u-exp-1', 'exp_user', 'admin', '2000-01-01T00:00:00+00:00')",
    )
    await db.conn.execute(
        "INSERT INTO sessions (token, user_id, username, role, expires_at)"
        " VALUES ('sess-valid', 'u-exp-1', 'exp_user', 'admin', '2099-01-01T00:00:00+00:00')",
    )
    await db.conn.commit()
    r = await client.post(
        "/mcp",
        cookies={"swarm_session": "sess-expired"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 71},
    )
    assert r.status_code == 401
    r = await client.post(
        "/mcp",
        cookies={"swarm_session": "sess-valid"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 72},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_gateway_session_rate_limit_429(test_env):
    """Retry1 P0-2: session path enforces the same sliding-window bucket (viewer 60rpm)."""
    client, _, db = test_env
    await db.conn.execute(
        "INSERT INTO users (id, username, role) VALUES ('u-rl-1', 'rl_viewer', 'viewer')"
    )
    await db.conn.execute(
        "INSERT INTO sessions (token, user_id, username, role, expires_at)"
        " VALUES ('sess-rl-viewer', 'u-rl-1', 'rl_viewer', 'viewer', '2099-01-01T00:00:00+00:00')",
    )
    await db.conn.commit()
    body = {"jsonrpc": "2.0", "method": "tools/list", "id": 81}
    statuses = []
    for _ in range(65):
        r = await client.post("/mcp", cookies={"swarm_session": "sess-rl-viewer"}, json=body)
        statuses.append(r.status_code)
    assert 429 in statuses


@pytest.mark.asyncio
async def test_gateway_audit_preserves_search_query(test_env):
    """Retry1 P0-3: web_search/deep_research query text preserved (not invalid-url)."""
    client, raw_key, db = test_env
    headers = {"Authorization": f"Bearer {raw_key}"}
    await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "web_search", "arguments": {"query": "forensic query xyz123"}},
            "id": 91,
        },
    )
    async with db.conn.execute(
        "SELECT target FROM audit_log WHERE action = 'web_search' ORDER BY id DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert "forensic query xyz123" in (row["target"] or "")
    assert row["target"] != "(invalid-url)"


@pytest.mark.asyncio
async def test_gateway_audit_logs_denied_calls(test_env):
    """Retry1 P0-3: 401/403/429 + unknown-tool -32601 leave audit rows, chain green."""
    client, raw_key, db = test_env
    search_only_raw = "swarm_sec_deniedprobe01"
    await _seed_agent_key(db, "k-denied-probe", search_only_raw, ["search"])
    # 403: search-only key attempts scrape
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {search_only_raw}"},
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "fetch_page", "arguments": {"url": "https://example.com"}},
            "id": 101,
        },
    )
    assert r.status_code == 403
    # 401: bad bearer
    r = await client.post(
        "/mcp",
        headers={"Authorization": "Bearer bad_token_xyz"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 102},
    )
    assert r.status_code == 401
    # unknown tool -32601
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "nope_tool_xyz", "arguments": {}},
            "id": 103,
        },
    )
    assert r.json()["error"]["code"] == -32601
    async with db.conn.execute("SELECT actor, action, target, details FROM audit_log") as cur:
        rows = await cur.fetchall()
    actions = [row["action"] for row in rows]
    assert "fetch_page_denied" in actions
    assert "auth_denied" in actions
    assert "unknown_tool" in actions
    assert all(str(r["actor"]).startswith("agent:") for r in rows)
    # zero secrets + chain green
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    for row in rows:
        blob = f"{row['actor']} {row['action']} {row['target']} {row['details']}"
        assert raw_key not in blob
        assert key_hash not in blob
        assert "Bearer" not in blob
    from panel_api.audit import AuditLogger

    assert await AuditLogger(db.conn).verify_chain() is True
