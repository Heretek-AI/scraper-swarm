"""Ticket #5: contract v1 — structured results, error codes, Retry-After, version header."""

from __future__ import annotations

import hashlib
import json
from hashlib import sha256
from pathlib import Path

import gateway.server as gw_server
import pytest
from httpx import ASGITransport, AsyncClient, Request, Response
from panel_api import ssrf_guard
from panel_api.db import Database

ENGINE_URLS = (
    "http://searxng:8080",
    "http://crawl4ai:11235",
    "http://scrapling:8000",
    "http://gpt-researcher:8000",
)


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

    # Real tool functions; DNS stubbed to a global IP so example.com passes
    # the pre-check deterministically without network.
    monkeypatch.setattr(gw_server, "DB_PATH", str(db_file))
    monkeypatch.setattr(ssrf_guard, "resolve_host", lambda host, timeout=3.0: ["93.184.216.34"])
    monkeypatch.delenv("SWARM_DEPLOYED_ENGINES", raising=False)
    gw_server._reset_rate_limits()

    transport = ASGITransport(app=gw_server.app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as client:
        yield client, raw_key, db

    gw_server._reset_rate_limits()
    await db.close()


def _call(tool: str, args: dict, rpc_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
        "id": rpc_id,
    }


class _SearxNGClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **k):
        return Response(
            200,
            json={
                "results": [
                    {"title": "T1", "url": "https://example.com/1", "content": "S1"},
                    {"title": "T2", "url": "https://example.com/2", "content": "S2"},
                ]
            },
            request=Request("GET", url),
        )


class _CrawlClient:
    MARKDOWN = "# Hello\n\nBody text here."

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        if url.endswith("/md"):
            return Response(200, json={"markdown": self.MARKDOWN}, request=Request("POST", url))
        raise AssertionError(f"unexpected POST {url}")


@pytest.mark.asyncio
async def test_web_search_structured_result(test_env, monkeypatch):
    client, raw_key, _ = test_env
    monkeypatch.setattr(gw_server.httpx, "AsyncClient", _SearxNGClient)
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_call("web_search", {"query": "q"}),
    )
    assert r.headers.get("X-Swarm-Contract") == "1"
    body = r.json()["result"]
    assert "Title: T1" in body["content"][0]["text"]  # legacy block kept
    structured = gw_server.WebSearchStructured.model_validate(body["structuredContent"])
    assert structured.query == "q"
    assert [(it.rank, it.title, it.url, it.snippet) for it in structured.results] == [
        (1, "T1", "https://example.com/1", "S1"),
        (2, "T2", "https://example.com/2", "S2"),
    ]
    assert structured.engine == "searxng"
    assert "isError" not in body


@pytest.mark.asyncio
async def test_fetch_page_structured_hash(test_env, monkeypatch):
    client, raw_key, _ = test_env
    monkeypatch.setattr(gw_server.httpx, "AsyncClient", _CrawlClient)
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_call("fetch_page", {"url": "https://example.com/"}),
    )
    body = r.json()["result"]
    assert _CrawlClient.MARKDOWN in body["content"][0]["text"]
    structured = gw_server.FetchPageStructured.model_validate(body["structuredContent"])
    assert structured.content_sha256 == sha256(structured.content.encode("utf-8")).hexdigest()
    assert structured.bytes == len(structured.content.encode("utf-8"))
    assert structured.format == "markdown"
    assert structured.requested_url == "https://example.com/"
    assert structured.engine == "crawl4ai"
    assert structured.truncated is False
    assert structured.title == "Hello"


@pytest.mark.asyncio
async def test_ssrf_denial_envelope(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_call("fetch_page", {"url": "http://169.254.169.254/"}),
    )
    body = r.json()["result"]
    assert body["isError"] is True
    assert body["structuredContent"]["code"] == "ssrf_denied"
    assert "SSRF denied" in body["content"][0]["text"]


@pytest.mark.asyncio
async def test_upstream_error_and_timeout_envelopes(test_env, monkeypatch):
    client, raw_key, _ = test_env

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **k):
            raise ConnectionError(f"dial {ENGINE_URLS[1]} refused")

    monkeypatch.setattr(gw_server.httpx, "AsyncClient", _Boom)
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_call("fetch_page", {"url": "https://example.com/"}),
    )
    body = r.json()["result"]
    assert body["isError"] is True
    assert body["structuredContent"]["code"] == "upstream_error"

    import httpx as _httpx

    class _Slow(_Boom):
        async def post(self, url, **k):
            raise _httpx.TimeoutException("timed out", request=Request("POST", url))

    monkeypatch.setattr(gw_server.httpx, "AsyncClient", _Slow)
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_call("fetch_page", {"url": "https://example.com/"}),
    )
    assert r.json()["result"]["structuredContent"]["code"] == "upstream_timeout"


@pytest.mark.asyncio
async def test_engine_unavailable_envelope(test_env, monkeypatch):
    client, raw_key, _ = test_env
    monkeypatch.setenv("SWARM_DEPLOYED_ENGINES", "searxng")
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_call("fetch_page", {"url": "https://example.com/"}),
    )
    body = r.json()["result"]
    assert body["isError"] is True
    assert body["structuredContent"]["code"] == "engine_unavailable"


@pytest.mark.asyncio
async def test_rate_limit_carries_retry_after(test_env, monkeypatch):
    client, _, db = test_env
    raw = "swarm_sec_ratelimit00002"
    await db.conn.execute(
        "INSERT INTO agent_keys (id, name, key_hash, key_prefix, scopes, rate_limit_rpm)"
        " VALUES ('k-rl', 'rl', ?, 'swarm_sec_rate...', ?, 1)",
        (hashlib.sha256(raw.encode()).hexdigest(), json.dumps(["search"])),
    )
    await db.conn.commit()
    headers = {"Authorization": f"Bearer {raw}"}
    body = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
    assert (await client.post("/mcp", headers=headers, json=body)).status_code == 200
    r = await client.post("/mcp", headers=headers, json=body)
    assert r.status_code == 429
    assert r.headers.get("X-Swarm-Contract") == "1"
    assert int(r.headers.get("Retry-After", "0")) >= 1


@pytest.mark.asyncio
async def test_no_internal_urls_in_error_messages(test_env, monkeypatch):
    client, raw_key, _ = test_env

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **k):
            raise ConnectionError(f"engine at {ENGINE_URLS[1]} exploded")

        async def get(self, url, **k):
            raise ConnectionError(f"engine at {ENGINE_URLS[0]} exploded")

    monkeypatch.setattr(gw_server.httpx, "AsyncClient", _Boom)
    texts: list[str] = []
    for tool, args in (
        ("fetch_page", {"url": "https://example.com/"}),
        ("web_search", {"query": "q"}),
        ("stealth_scrape", {"url": "https://example.com/"}),
        ("deep_research", {"query": "q"}),
    ):
        r = await client.post(
            "/mcp", headers={"Authorization": f"Bearer {raw_key}"}, json=_call(tool, args)
        )
        payload = r.json().get("result", {})
        texts.append(payload.get("content", [{}])[0].get("text", ""))
        if "structuredContent" in payload:
            texts.append(json.dumps(payload["structuredContent"]))
    blob = "\n".join(texts)
    for internal in ENGINE_URLS:
        assert internal not in blob, internal


@pytest.mark.asyncio
async def test_contract_version_in_initialize(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
    )
    assert r.headers.get("X-Swarm-Contract") == "1"
    assert r.json()["result"]["serverInfo"]["contractVersion"] == 1


@pytest.mark.asyncio
async def test_output_schemas_advertised(test_env):
    client, raw_key, _ = test_env
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
    )
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert "outputSchema" in tools["web_search"]
    assert "outputSchema" in tools["fetch_page"]
    assert "outputSchema" not in tools["deep_research"]
    assert "outputSchema" not in tools["stealth_scrape"]


@pytest.mark.asyncio
async def test_fetch_page_crawl_fallback_and_truncation(test_env, monkeypatch):
    client, raw_key, _ = test_env
    monkeypatch.setattr(gw_server, "MAX_FETCH_BYTES", 10)

    class _Fallback:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **k):
            if url.endswith("/md"):
                return Response(500, text="err", request=Request("POST", url))
            return Response(
                200,
                json={
                    "results": [
                        {
                            "html": "<p>hello world fallback</p>",
                            "url": "https://example.com/final",
                            "status_code": 200,
                            "content_type": "text/html",
                        }
                    ]
                },
                request=Request("POST", url),
            )

    monkeypatch.setattr(gw_server.httpx, "AsyncClient", _Fallback)
    r = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=_call("fetch_page", {"url": "https://example.com/"}),
    )
    structured = gw_server.FetchPageStructured.model_validate(
        r.json()["result"]["structuredContent"]
    )
    assert structured.format == "html"
    assert structured.final_url == "https://example.com/final"
    assert structured.truncated is True
    assert structured.bytes == len(structured.content.encode("utf-8"))
    assert structured.content_sha256 == sha256(structured.content.encode("utf-8")).hexdigest()
