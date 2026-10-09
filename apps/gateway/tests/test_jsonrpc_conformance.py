"""Ticket #4: JSON-RPC 2.0 / MCP conformance for POST /mcp."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import gateway
import gateway.server as gw_server
import pytest
from httpx import ASGITransport, AsyncClient
from panel_api.db import Database
from pydantic import ValidationError


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
    # Engine filter default: all deployed unless the test overrides the env.
    monkeypatch.delenv("SWARM_DEPLOYED_ENGINES", raising=False)
    gw_server._reset_rate_limits()

    transport = ASGITransport(app=gw_server.app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as client:
        yield client, raw_key, db

    gw_server._reset_rate_limits()
    await db.close()


async def _seed_key(db, key_id, raw, scopes, rpm=60):
    await db.conn.execute(
        """
        INSERT INTO agent_keys (id, name, key_hash, key_prefix, scopes, rate_limit_rpm)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            key_id,
            f"agent-{key_id}",
            hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            raw[:14] + "...",
            json.dumps(scopes),
            rpm,
        ),
    )
    await db.conn.commit()


@pytest.mark.asyncio
async def test_parse_error_malformed_json(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}", "Content-Type": "application/json"},
        content=b'{"jsonrpc": "2.0", "method": ',
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"]["code"] == -32700
    assert body["id"] is None


@pytest.mark.asyncio
async def test_invalid_request_missing_jsonrpc(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"method": "tools/list", "id": 1},
    )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_invalid_request_bad_method_type(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"jsonrpc": "2.0", "method": 123, "id": 1},
    )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_unknown_method_jsonrpc_error(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"jsonrpc": "2.0", "method": "tools/frobnicate", "id": 7},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["error"]["code"] == -32601
    assert body["id"] == 7


@pytest.mark.asyncio
async def test_notification_returns_202_no_body(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"jsonrpc": "2.0", "method": "tools/list"},
    )
    assert r.status_code == 202
    assert r.content == b""


@pytest.mark.asyncio
async def test_id_echoed_exactly_string_id(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": "abc-123"},
    )
    assert r.json()["id"] == "abc-123"


@pytest.mark.asyncio
async def test_initialize_negotiates_supported_version(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
            "id": 1,
        },
    )
    assert r.json()["result"]["protocolVersion"] == "2025-03-26"


@pytest.mark.asyncio
async def test_initialize_falls_back_to_latest_on_unknown_version(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01"},
            "id": 1,
        },
    )
    assert r.json()["result"]["protocolVersion"] == gw_server.LATEST_PROTOCOL_VERSION


@pytest.mark.asyncio
async def test_version_single_source(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )
    assert r.json()["result"]["serverInfo"]["version"] == gateway.__version__
    assert gw_server.app.version == gateway.__version__
    pyproject = Path(gw_server.__file__).resolve().parents[2] / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text())["project"]["version"] == gateway.__version__


@pytest.mark.asyncio
async def test_tools_list_filtered_by_scope(test_env):
    client, _, db = test_env
    search_raw = "swarm_sec_searchonly44441"
    await _seed_key(db, "k-so", search_raw, ["search"])
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {search_raw}"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
    )
    names = [t["name"] for t in r.json()["result"]["tools"]]
    assert "web_search" in names and "deep_research" in names
    assert "fetch_page" not in names and "stealth_scrape" not in names

    scrape_raw = "swarm_sec_scrapeonly44441"
    await _seed_key(db, "k-sc", scrape_raw, ["scrape"])
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {scrape_raw}"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 2},
    )
    names = [t["name"] for t in r.json()["result"]["tools"]]
    assert "fetch_page" in names and "stealth_scrape" in names
    assert "web_search" not in names and "deep_research" not in names


@pytest.mark.asyncio
async def test_tools_list_filtered_by_deployed_engines(test_env, monkeypatch):
    client, raw_key, _ = test_env
    monkeypatch.setenv("SWARM_DEPLOYED_ENGINES", "searxng")
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
    )
    names = [t["name"] for t in r.json()["result"]["tools"]]
    assert names == ["web_search"]


@pytest.mark.asyncio
async def test_tools_list_schemas_generated_from_models(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
    )
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    schema = tools["web_search"]["inputSchema"]
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) >= {"query", "limit"}
    assert tools["fetch_page"]["inputSchema"]["required"] == ["url"]
    # The served schema validates what the handler accepts.
    gw_server.WebSearchArgs.model_validate({"query": "q", "limit": 3})
    with pytest.raises(ValidationError):
        gw_server.WebSearchArgs.model_validate({"limit": 3})


@pytest.mark.asyncio
async def test_tools_call_invalid_params_32602(test_env):
    client, raw_key, _ = test_env
    headers = {"Authorization": f"Bearer {raw_key}"}
    r = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "web_search", "arguments": {"limit": 3}},
            "id": 1,
        },
    )
    assert r.json()["error"]["code"] == -32602
    r = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "fetch_page", "arguments": {}},
            "id": 2,
        },
    )
    assert r.json()["error"]["code"] == -32602
    # Unknown tool keeps -32601.
    r = await client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "nope_tool", "arguments": {}},
            "id": 3,
        },
    )
    assert r.json()["error"]["code"] == -32601
