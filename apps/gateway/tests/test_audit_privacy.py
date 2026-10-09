"""Ticket #10: audit query privacy modes (verbatim/hashed/redacted)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import gateway.server as gw_server
import pytest
from panel_api.audit import AuditLogger
from panel_api.db import Database


@pytest.fixture
async def test_env(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "panel.db"
    db = Database(db_file)
    await db.connect()

    async def seed(name: str, mode: str | None):
        raw = f"swarm_sec_{name}0000001"
        await db.conn.execute(
            "INSERT INTO agent_keys (id, name, key_hash, key_prefix, scopes, rate_limit_rpm, audit_query_mode)"
            " VALUES (?, ?, ?, ?, ?, 60, ?)",
            (
                f"k-{name}",
                name,
                hashlib.sha256(raw.encode()).hexdigest(),
                raw[:14] + "...",
                json.dumps(["search", "scrape"]),
                mode,
            ),
        )
        await db.conn.commit()

    await seed("verbatim-agent", "verbatim")
    await seed("hashed-agent", "hashed")
    await seed("redacted-agent", "redacted")
    await seed("default-agent", None)

    monkeypatch.setattr(gw_server, "DB_PATH", str(db_file))
    monkeypatch.setenv("SWARM_AUDIT_HMAC_SALT", "test-salt")
    monkeypatch.delenv("SWARM_AUDIT_QUERY_MODE", raising=False)
    gw_server._reset_rate_limits()
    yield db
    gw_server._reset_rate_limits()
    await db.close()


async def _last_target(db) -> str | None:
    async with db.conn.execute("SELECT target FROM audit_log ORDER BY id DESC LIMIT 1") as cur:
        row = await cur.fetchone()
    return row["target"] if row else None


@pytest.mark.asyncio
async def test_verbatim_mode_preserves_query(test_env):
    db = test_env
    await gw_server.log_agent_activity("verbatim-agent", "web_search", target="forensic query xyz")
    assert await _last_target(db) == "forensic query xyz"


@pytest.mark.asyncio
async def test_hashed_mode_stores_hmac_not_query(test_env):
    db = test_env
    await gw_server.log_agent_activity("hashed-agent", "web_search", target="secret research topic")
    target = await _last_target(db)
    assert target is not None and target.startswith("hmac-sha256:")
    assert len(target) == len("hmac-sha256:") + 64
    assert "secret research topic" not in target
    # Equal queries correlate without being revealed.
    await gw_server.log_agent_activity("hashed-agent", "web_search", target="secret research topic")
    assert await _last_target(db) == target
    await gw_server.log_agent_activity("hashed-agent", "web_search", target="another topic")
    assert await _last_target(db) != target
    assert await AuditLogger(db.conn).verify_chain() is True


@pytest.mark.asyncio
async def test_redacted_mode_stores_length_only(test_env):
    db = test_env
    await gw_server.log_agent_activity(
        "redacted-agent", "web_search", target="secret research topic"
    )
    target = await _last_target(db)
    assert target is not None and "secret research topic" not in target
    assert "redacted" in target and str(len("secret research topic")) in target
    assert await AuditLogger(db.conn).verify_chain() is True


@pytest.mark.asyncio
async def test_default_mode_follows_global_env(test_env, monkeypatch):
    db = test_env
    await gw_server.log_agent_activity("default-agent", "web_search", target="plain query")
    assert await _last_target(db) == "plain query"
    monkeypatch.setenv("SWARM_AUDIT_QUERY_MODE", "hashed")
    await gw_server.log_agent_activity("default-agent", "web_search", target="plain query")
    assert (await _last_target(db) or "").startswith("hmac-sha256:")
    assert await AuditLogger(db.conn).verify_chain() is True


@pytest.mark.asyncio
async def test_unknown_mode_fails_closed_to_redacted(test_env):
    db = test_env
    await db.conn.execute(
        "UPDATE agent_keys SET audit_query_mode = 'bogus' WHERE name = 'verbatim-agent'"
    )
    await db.conn.commit()
    await gw_server.log_agent_activity("verbatim-agent", "web_search", target="some query")
    target = await _last_target(db)
    assert target is not None and "some query" not in target
    assert await AuditLogger(db.conn).verify_chain() is True


@pytest.mark.asyncio
async def test_hashed_without_salt_falls_back_to_redacted(test_env, monkeypatch):
    db = test_env
    monkeypatch.delenv("SWARM_AUDIT_HMAC_SALT", raising=False)
    await gw_server.log_agent_activity("hashed-agent", "web_search", target="some query")
    target = await _last_target(db)
    assert target is not None and "some query" not in target
    assert "redacted" in target


@pytest.mark.asyncio
async def test_tamper_evident_in_hashed_mode(test_env):
    """QA: flipping one stored hash breaks verification (chain still meaningful)."""
    db = test_env
    await gw_server.log_agent_activity("hashed-agent", "web_search", target="q")
    assert await AuditLogger(db.conn).verify_chain() is True
    await db.conn.execute("UPDATE audit_log SET target = 'hmac-sha256:dead'")
    await db.conn.commit()
    assert await AuditLogger(db.conn).verify_chain() is False
