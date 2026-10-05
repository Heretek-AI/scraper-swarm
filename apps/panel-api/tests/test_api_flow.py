"""End-to-end API test covering bootstrap, TOTP login, catalog, deploy, and agent keys."""

from __future__ import annotations

import os
from pathlib import Path
import pyotp
import pytest
from httpx import ASGITransport, AsyncClient

import panel_api.main as app_main
from panel_api.db import Database
from panel_api.routers.auth import BOOTSTRAP_STATE_KEY, SESSIONS
from panel_api.swarmd_client import SwarmdClient
from panel_api.vault import Vault
from swarmd.catalog import load_catalog
from swarmd.server import SwarmdServer


@pytest.fixture
async def client_and_ctx(tmp_path: Path):
    SESSIONS.clear()
    repo_root = Path(__file__).resolve().parents[3]
    db_file = tmp_path / "panel.db"
    key_file = tmp_path / "master.key"
    socket_path = tmp_path / "swarmd.sock"
    stack_dir = tmp_path / "stack"

    db = Database(db_file)
    await db.connect()

    # Pre-seed bootstrap token
    bootstrap_token = "test-bootstrap-token-12345"
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
        yield ac, bootstrap_token, db

    await swarmd_server.stop()
    await db.close()


@pytest.mark.asyncio
async def test_full_wizard_and_auth_workflow(client_and_ctx):
    client, bootstrap_token, db = client_and_ctx

    # 1. Check initial status
    r = await client.get("/auth/status")
    assert r.status_code == 200
    assert r.json() == {"setup_completed": False}

    # 2. Try unauthenticated call to catalog -> 401
    r = await client.get("/services/catalog")
    assert r.status_code == 401

    # 3. Bootstrap init admin
    r = await client.post(
        "/auth/bootstrap-init",
        json={"token": bootstrap_token, "admin_username": "john_admin"},
    )
    assert r.status_code == 200
    totp_secret = r.json()["totp_secret"]

    # 4. Verify TOTP to complete setup & establish session
    code = pyotp.TOTP(totp_secret).now()
    r = await client.post(
        "/auth/verify-totp",
        json={"username": "john_admin", "code": code},
    )
    assert r.status_code == 200
    cookie_val = r.cookies.get("swarm_session")
    assert cookie_val is not None
    client.cookies.set("swarm_session", cookie_val)

    # 5. Check /auth/me
    r = await client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == "john_admin"
    assert r.json()["role"] == "admin"

    # 6. Fetch catalog
    r = await client.get("/services/catalog")
    assert r.status_code == 200
    catalog = r.json()
    assert any(s["id"] == "searxng" for s in catalog)

    # 7. Deploy SearXNG & Crawl4AI
    r = await client.post(
        "/services/deploy",
        json=[
            {"service_id": "searxng", "profile": "standard"},
            {"service_id": "crawl4ai", "profile": "standard"},
        ],
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # 8. Check installed services
    r = await client.get("/services/installed")
    assert r.status_code == 200
    installed = r.json()
    assert len(installed) == 2

    # 9. Create OpenCode agent key
    r = await client.post(
        "/agents/keys",
        json={"name": "coding-agent-1", "scopes": ["search", "scrape"]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "coding-agent-1"
    assert data["raw_key"].startswith("swarm_sec_")
    assert "mcp" in data["opencode_snippet"]
    key_id = data["id"]

    # 10. List agent keys
    r = await client.get("/agents/keys")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # 11. Revoke agent key
    r = await client.delete(f"/agents/keys/{key_id}")
    assert r.status_code == 200

    r = await client.get("/agents/keys")
    assert len(r.json()) == 0

    # 12. Verify Security Posture score and checks
    r = await client.get("/security/posture")
    assert r.status_code == 200
    posture = r.json()
    assert posture["score"] >= 80
    assert posture["admin_2fa_enforced"] is True
    assert posture["egress_default_deny"] is True
    assert posture["audit_chain_valid"] is True
    assert len(posture["details"]) > 0
