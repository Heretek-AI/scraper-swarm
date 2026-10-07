"""Phase 03-opencode-integration agent-key issuance tests.

Evidence:
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P2-C1-C2-C3
- file:///home/john/Projects/scraper-swarm/apps/panel-api/src/panel_api/routers/agents.py
- file:///home/john/Projects/scraper-swarm/apps/gateway/src/gateway/server.py

AC1: scoped key via live API with one-time raw_key + prefix + valid
opencode_snippet, no extra leakage. AC5: scope validation fail-closed.
"""

from __future__ import annotations

from pathlib import Path

import panel_api.main as app_main
import pyotp
import pytest
from httpx import ASGITransport, AsyncClient
from panel_api.db import Database
from panel_api.routers.auth import BOOTSTRAP_STATE_KEY, SESSIONS
from panel_api.swarmd_client import SwarmdClient
from panel_api.vault import Vault
from swarmd.catalog import load_catalog
from swarmd.server import SwarmdServer


@pytest.fixture
async def authed_client(tmp_path: Path):
    SESSIONS.clear()
    repo_root = Path(__file__).resolve().parents[3]
    db_file = tmp_path / "panel.db"
    key_file = tmp_path / "master.key"
    socket_path = tmp_path / "swarmd.sock"
    stack_dir = tmp_path / "stack"

    db = Database(db_file)
    await db.connect()

    bootstrap_token = "test-bootstrap-token-0303"
    await db.conn.execute(
        "INSERT INTO system_state (key, value) VALUES (?, ?)",
        (BOOTSTRAP_STATE_KEY, bootstrap_token),
    )
    await db.conn.commit()

    vault = Vault.from_file(key_file, auto_create=True)
    swarmd_client = SwarmdClient(socket_path)
    catalog = load_catalog(repo_root / "catalog" / "services")

    swarmd_server = SwarmdServer(
        socket_path=socket_path,
        catalog=catalog,
        stack_dir=stack_dir,
        repo_root=repo_root,
        allow_draft=True,
    )
    await swarmd_server.start()

    app_main.app_state = app_main.AppState(
        db=db,
        vault=vault,
        swarmd=swarmd_client,
        catalog=catalog,
    )

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Bootstrap admin + session cookie.
        r = await ac.post(
            "/auth/bootstrap-init",
            json={"token": bootstrap_token, "admin_username": "opencode_admin"},
        )
        assert r.status_code == 200
        code = pyotp.TOTP(r.json()["totp_secret"]).now()
        r = await ac.post("/auth/verify-totp", json={"username": "opencode_admin", "code": code})
        assert r.status_code == 200
        cookie_val = r.cookies.get("swarm_session")
        assert cookie_val is not None
        ac.cookies.set("swarm_session", cookie_val)
        yield ac, db

    await swarmd_server.stop()
    await db.close()


@pytest.mark.asyncio
async def test_create_key_returns_once_snippet_and_prefix(authed_client):
    """AC1: raw_key one-time + prefix + valid opencode snippet with remote MCP URL."""
    client, _ = authed_client
    r = await client.post(
        "/agents/keys",
        json={"name": "opencode-coder-1", "scopes": ["search", "scrape"], "expires_in_hours": 24},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["raw_key"].startswith("swarm_sec_")
    assert data["key_prefix"].startswith("swarm_sec_")
    assert data["raw_key"] not in data["key_prefix"]
    assert data["expires_at"] is not None
    snippet = data["opencode_snippet"]
    assert snippet["plugin"] == ["@scraper-swarm/opencode-plugin"]
    mcp = snippet["mcp"]["scraper-swarm"]
    assert mcp["type"] == "remote"
    assert mcp["url"].endswith("/mcp")
    assert mcp["headers"]["Authorization"] == f"Bearer {data['raw_key']}"


@pytest.mark.asyncio
async def test_create_key_rejects_unknown_and_empty_scopes(authed_client):
    """AC5: issuance validates scopes fail-closed."""
    client, _ = authed_client
    r = await client.post("/agents/keys", json={"name": "bad", "scopes": ["search", "root"]})
    assert r.status_code == 422
    r = await client.post("/agents/keys", json={"name": "empty", "scopes": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_key_list_and_audit_carry_no_secrets(authed_client):
    """AC1/AC6: list endpoint and audit log expose prefix only — never raw/hash."""
    client, db = authed_client
    r = await client.post("/agents/keys", json={"name": "leak-probe", "scopes": ["search"]})
    assert r.status_code == 200
    raw_key = r.json()["raw_key"]

    r = await client.get("/agents/keys")
    assert r.status_code == 200
    body = r.text
    assert raw_key not in body
    assert "key_hash" not in body
    assert "raw_key" not in body

    async with db.conn.execute(
        "SELECT actor, action, target, details FROM audit_log WHERE action = 'create_agent_key'"
    ) as cur:
        rows = await cur.fetchall()
    assert len(rows) >= 1
    for row in rows:
        blob = f"{row['actor']} {row['action']} {row['target']} {row['details']}"
        assert raw_key not in blob
        assert "key_hash" not in blob.lower()


@pytest.mark.asyncio
async def test_revoked_key_disappears_from_list(authed_client):
    client, _ = authed_client
    r = await client.post("/agents/keys", json={"name": "temp-key", "scopes": ["search"]})
    key_id = r.json()["id"]
    assert (await client.delete(f"/agents/keys/{key_id}")).status_code == 200
    r = await client.get("/agents/keys")
    assert all(k["id"] != key_id for k in r.json())
    assert (await client.delete(f"/agents/keys/{key_id}")).status_code == 404
