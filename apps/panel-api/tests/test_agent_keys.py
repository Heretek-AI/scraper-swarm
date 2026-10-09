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
async def authed_client(tmp_path: Path, monkeypatch):
    SESSIONS.clear()
    # Phase 03 retry2 QA-B P0-5: default tests use a configured host so the
    # dead-host ack gate does not interfere; the placeholder test unsets it.
    monkeypatch.setenv("SWARM_PUBLIC_MCP_URL", "https://test.local/mcp")
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


@pytest.mark.asyncio
async def test_create_key_rejects_duplicate_name_409(authed_client):
    """Retry1 P0-5: duplicate key names rejected with 409."""
    client, _ = authed_client
    r = await client.post("/agents/keys", json={"name": "dup-key-1", "scopes": ["search"]})
    assert r.status_code == 200
    r = await client.post("/agents/keys", json={"name": "dup-key-1", "scopes": ["search"]})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_key_dedupes_scopes(authed_client):
    """Retry1 P0-5: duplicate scopes deduped preserving order."""
    client, _ = authed_client
    r = await client.post(
        "/agents/keys", json={"name": "dedupe-key-1", "scopes": ["search", "search", "scrape"]}
    )
    assert r.status_code == 200
    assert r.json()["scopes"] == ["search", "scrape"]


@pytest.mark.asyncio
async def test_create_key_infinite_requires_ack_and_defaults_ttl(authed_client):
    """Retry1 P0-5: None without ack -> 422; default TTL finite; ack allows infinite."""
    client, _ = authed_client
    r = await client.post(
        "/agents/keys", json={"name": "inf-noack", "scopes": ["search"], "expires_in_hours": None}
    )
    assert r.status_code == 422
    r = await client.post("/agents/keys", json={"name": "default-ttl", "scopes": ["search"]})
    assert r.status_code == 200
    assert r.json()["expires_at"] is not None
    r = await client.post(
        "/agents/keys",
        json={
            "name": "inf-ack",
            "scopes": ["search"],
            "expires_in_hours": None,
            "allow_never_expire": True,
        },
    )
    assert r.status_code == 200
    assert r.json()["expires_at"] is None


@pytest.mark.asyncio
async def test_snippet_placeholder_warns_when_host_unconfigured(authed_client, monkeypatch):
    """Retry2 P0-5: dead-host placeholder blocks without ack, warns once acked."""
    client, _ = authed_client
    monkeypatch.delenv("SWARM_PUBLIC_MCP_URL", raising=False)
    # Without ack: blocked 422 before any live key ships.
    r = await client.post("/agents/keys", json={"name": "snippet-place", "scopes": ["search"]})
    assert r.status_code == 422
    assert "REPLACE-ME" in r.text or "placeholder" in r.text.lower()
    # With explicit ack: 200 + conspicuous REPLACE-ME + warning (request-time bind).
    r = await client.post(
        "/agents/keys",
        json={"name": "snippet-place", "scopes": ["search"], "allow_placeholder_host": True},
    )
    assert r.status_code == 200
    snippet = r.json()["opencode_snippet"]
    assert snippet["mcp"]["scraper-swarm"]["url"].endswith("/mcp")
    assert "REPLACE-ME" in snippet["mcp"]["scraper-swarm"]["url"]
    assert "warning" in snippet
    monkeypatch.setenv("SWARM_PUBLIC_MCP_URL", "https://myhost.local/mcp")
    r = await client.post("/agents/keys", json={"name": "snippet-real", "scopes": ["search"]})
    assert r.json()["opencode_snippet"]["mcp"]["scraper-swarm"]["url"] == "https://myhost.local/mcp"
    assert "warning" not in r.json()["opencode_snippet"]
    # http:// cleartext host warns bearer-over-cleartext (still 200).
    monkeypatch.setenv("SWARM_PUBLIC_MCP_URL", "http://myhost.local/mcp")
    r = await client.post("/agents/keys", json={"name": "snippet-http", "scopes": ["search"]})
    assert r.status_code == 200
    assert "warning" in r.json()["opencode_snippet"]
    assert "cleartext" in r.json()["opencode_snippet"]["warning"].lower()


@pytest.mark.asyncio
async def test_create_key_rejects_whitespace_name_and_case_dup_409(authed_client):
    """Retry2 P0-5: whitespace-only 422; case-variant dup 409 (NOCASE)."""
    client, _ = authed_client
    r = await client.post("/agents/keys", json={"name": "   ", "scopes": ["search"]})
    assert r.status_code == 422
    r = await client.post("/agents/keys", json={"name": "CaseKey", "scopes": ["search"]})
    assert r.status_code == 200
    r = await client.post("/agents/keys", json={"name": "casekey", "scopes": ["search"]})
    assert r.status_code == 409
    r = await client.post("/agents/keys", json={"name": "  CaseKey  ", "scopes": ["search"]})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_key_concurrent_same_name_single_winner(authed_client):
    """Retry2 P0-5: concurrent same-name inserts -> exactly one 200, rest 409 (no hang)."""
    import asyncio

    client, _ = authed_client
    results = await asyncio.gather(
        *(
            client.post("/agents/keys", json={"name": "race-key-1", "scopes": ["search"]})
            for _ in range(3)
        )
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1
    assert codes.count(409) == 2


@pytest.mark.asyncio
async def test_panel_session_expiry_401(authed_client):
    """Retry1 P0-1: panel get_current_user honors expired sessions with 401."""
    from panel_api.routers.auth import SESSIONS

    client, db = authed_client
    # Insert an expired DB session (bypass in-memory cache).
    await db.conn.execute(
        "INSERT INTO users (id, username, role) VALUES ('u-exp-panel', 'exp_panel', 'viewer')"
    )
    await db.conn.execute(
        "INSERT INTO sessions (token, user_id, username, role, expires_at)"
        " VALUES ('sess-panel-expired', 'u-exp-panel', 'exp_panel', 'viewer',"
        " '2000-01-01T00:00:00+00:00')",
    )
    await db.conn.commit()
    SESSIONS.pop("sess-panel-expired", None)
    client.cookies.set("swarm_session", "sess-panel-expired")
    r = await client.get("/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_panel_session_null_empty_naive_fail_closed(authed_client):
    """Retry2 P0-1: NULL/''/whitespace/naive-past sessions 401; valid future 200."""
    from panel_api.routers.auth import SESSIONS

    client, db = authed_client
    await db.conn.execute(
        "INSERT INTO users (id, username, role) VALUES ('u-null-1', 'null_user', 'viewer')"
    )
    cases = [
        ("sess-null", None),
        ("sess-empty", ""),
        ("sess-ws", "   "),
        ("sess-naive-past", "2000-01-01T00:00:00"),
        ("sess-unparseable", "not-a-date"),
    ]
    for token, exp in cases:
        await db.conn.execute(
            "INSERT OR REPLACE INTO sessions (token, user_id, username, role, expires_at)"
            " VALUES (?, 'u-null-1', 'null_user', 'viewer', ?)",
            (token, exp),
        )
    await db.conn.execute(
        "INSERT OR REPLACE INTO sessions (token, user_id, username, role, expires_at)"
        " VALUES ('sess-valid', 'u-null-1', 'null_user', 'viewer', '2099-01-01T00:00:00+00:00')",
    )
    await db.conn.commit()
    for token, _ in cases:
        SESSIONS.pop(token, None)
        client.cookies.set("swarm_session", token)
        r = await client.get("/auth/me")
        assert r.status_code == 401, token
    SESSIONS.pop("sess-valid", None)
    client.cookies.set("swarm_session", "sess-valid")
    r = await client.get("/auth/me")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_create_key_audit_mode_option(authed_client):
    """Ticket #10: per-key audit mode stored, listed, validated."""
    client, _ = authed_client
    r = await client.post(
        "/agents/keys",
        json={"name": "priv-svc", "scopes": ["search"], "audit_query_mode": "hashed"},
    )
    assert r.status_code == 200
    assert r.json()["audit_query_mode"] == "hashed"
    r = await client.get("/agents/keys")
    modes = {k["name"]: k["audit_query_mode"] for k in r.json()}
    assert modes["priv-svc"] == "hashed"
    r = await client.post(
        "/agents/keys",
        json={"name": "priv-bad", "scopes": ["search"], "audit_query_mode": "bogus"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_security_privacy_endpoint(authed_client, monkeypatch):
    """Ticket #10: operator-visible privacy posture."""
    client, _ = authed_client
    monkeypatch.setenv("SWARM_AUDIT_QUERY_MODE", "redacted")
    monkeypatch.setenv("SWARM_AUDIT_RETENTION_DAYS", "90")
    monkeypatch.delenv("SWARM_AUDIT_HMAC_SALT", raising=False)
    r = await client.get("/security/audit/privacy")
    assert r.status_code == 200
    body = r.json()
    assert body["query_mode_default"] == "redacted"
    assert body["retention_days"] == 90
    assert body["hmac_salt_configured"] is False
