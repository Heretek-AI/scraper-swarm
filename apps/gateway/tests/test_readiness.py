"""Ticket #7: deep readiness endpoint and its Caddy routing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import gateway.server as gw_server
import pytest
from httpx import ASGITransport, AsyncClient, Request, Response
from panel_api.db import Database


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

    monkeypatch.setattr(gw_server, "DB_PATH", str(db_file))
    gw_server._reset_rate_limits()

    transport = ASGITransport(app=gw_server.app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as client:
        yield client, raw_key, db

    gw_server._reset_rate_limits()
    await db.close()


class _FakeEngines:
    """httpx.AsyncClient stand-in: searxng+crawl4ai healthy, scrapling down."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **k):
        if "scrapling" in url:
            raise ConnectionError("refused")
        return Response(200, text="ok", request=Request("GET", url))


@pytest.mark.asyncio
async def test_ready_reports_engines_and_db(test_env, monkeypatch):
    client, _, _ = test_env
    monkeypatch.setattr(gw_server.httpx, "AsyncClient", _FakeEngines)
    r = await client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["db"]["ok"] is True
    assert body["engines"]["searxng"]["ok"] is True
    assert body["engines"]["crawl4ai"]["ok"] is True
    assert body["engines"]["scrapling"]["ok"] is False
    assert body["ready"] is False  # one engine down -> not ready
    assert isinstance(body["engines"]["searxng"]["ms"], int)


@pytest.mark.asyncio
async def test_ready_all_healthy(test_env, monkeypatch):
    client, _, _ = test_env

    class _AllOk(_FakeEngines):
        async def get(self, url, **k):
            return Response(200, text="ok", request=Request("GET", url))

    monkeypatch.setattr(gw_server.httpx, "AsyncClient", _AllOk)
    r = await client.get("/ready")
    assert r.json()["ready"] is True


@pytest.mark.asyncio
async def test_ready_db_missing_reports_not_ready(test_env, monkeypatch):
    client, _, _ = test_env
    monkeypatch.setattr(gw_server, "DB_PATH", "/nonexistent/panel.db")
    monkeypatch.setattr(gw_server.httpx, "AsyncClient", _FakeEngines)
    r = await client.get("/ready")
    assert r.json()["ready"] is False
    assert r.json()["db"]["ok"] is False


def test_caddy_routes_ready_and_health_to_gateway():
    caddyfile = Path(gw_server.__file__).resolve().parents[4] / "deploy" / "Caddyfile"
    text = caddyfile.read_text()
    for route in ("handle /ready", "handle /health"):
        assert route in text, route
    assert text.count("reverse_proxy gateway:8000") >= 4
