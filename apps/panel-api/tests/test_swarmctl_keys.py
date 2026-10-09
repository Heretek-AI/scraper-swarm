"""Ticket #7: service-account keys (swarmctl CLI), rotation, last_used_at."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from panel_api.db import Database
from panel_api.swarmctl import amain as swarmctl_amain


async def _run(argv: list[str]) -> int:
    return await swarmctl_amain(argv)


def _row_by_name(db, name):
    async def _get():
        async with db.conn.execute(
            "SELECT id, name, key_hash, scopes, rate_limit_rpm, expires_at, last_used_at"
            " FROM agent_keys WHERE name = ? COLLATE NOCASE",
            (name,),
        ) as cur:
            return await cur.fetchone()

    return _get()


@pytest.mark.asyncio
async def test_cli_creates_scoped_key_with_expiry(tmp_path: Path, capsys):
    db_file = tmp_path / "panel.db"
    rc = await _run(
        [
            "--db",
            str(db_file),
            "keys",
            "create",
            "--name",
            "research-svc",
            "--scopes",
            "search,scrape",
            "--expires",
            "90d",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    m = re.search(r"swarm_sec_[0-9a-f]+", out)
    assert m, "raw key must be printed once"

    db = Database(db_file)
    await db.connect()
    row = await _row_by_name(db, "research-svc")
    assert row is not None
    assert sorted(json.loads(row["scopes"])) == ["scrape", "search"]
    exp = datetime.fromisoformat(row["expires_at"])
    assert timedelta(days=89) < exp - datetime.now(UTC) < timedelta(days=91)
    # Only the hash is stored.
    assert m.group(0) not in (row["key_hash"] or "")
    assert row["key_hash"] == hashlib.sha256(m.group(0).encode()).hexdigest()
    await db.close()


@pytest.mark.asyncio
async def test_cli_create_rejects_unknown_scope_and_dup_name(tmp_path: Path, capsys):
    db_file = tmp_path / "panel.db"
    assert (
        await _run(["--db", str(db_file), "keys", "create", "--name", "a", "--scopes", "nope"]) != 0
    )
    assert (
        await _run(["--db", str(db_file), "keys", "create", "--name", "dup", "--scopes", "search"])
        == 0
    )
    capsys.readouterr()
    assert (
        await _run(["--db", str(db_file), "keys", "create", "--name", "DUP", "--scopes", "search"])
        != 0
    )


@pytest.mark.asyncio
async def test_cli_rotate_grace_period(tmp_path: Path, capsys):
    """Old key stays valid during grace, then fails; new key keeps working."""
    import gateway.server as gw_server

    db_file = tmp_path / "panel.db"
    assert (
        await _run(
            [
                "--db",
                str(db_file),
                "keys",
                "create",
                "--name",
                "svc",
                "--scopes",
                "search,scrape",
                "--expires",
                "90d",
            ]
        )
        == 0
    )
    old_raw = re.search(r"swarm_sec_[0-9a-f]+", capsys.readouterr().out).group(0)

    assert await _run(["--db", str(db_file), "keys", "rotate", "svc", "--grace", "24h"]) == 0
    new_raw = re.search(r"swarm_sec_[0-9a-f]+", capsys.readouterr().out).group(0)
    assert new_raw != old_raw

    gw_server.DB_PATH = str(db_file)
    gw_server._reset_rate_limits()
    try:
        # During grace both verify.
        await gw_server.verify_agent_token(f"Bearer {old_raw}", None, "search")
        await gw_server.verify_agent_token(f"Bearer {new_raw}", None, "scrape")
        # After grace the old key is rejected, the new one still passes.
        db = Database(db_file)
        await db.connect()
        await db.conn.execute(
            "UPDATE agent_keys SET expires_at = '2000-01-01T00:00:00+00:00' WHERE key_hash = ?",
            (hashlib.sha256(old_raw.encode()).hexdigest(),),
        )
        await db.conn.commit()
        await db.close()
        with pytest.raises(HTTPException):
            await gw_server.verify_agent_token(f"Bearer {old_raw}", None, "search")
        await gw_server.verify_agent_token(f"Bearer {new_raw}", None, "search")
    finally:
        gw_server._reset_rate_limits()


@pytest.mark.asyncio
async def test_verify_updates_last_used_at(tmp_path: Path):
    import gateway.server as gw_server

    db_file = tmp_path / "panel.db"
    db = Database(db_file)
    await db.connect()
    raw = "swarm_sec_lastused00001"
    await db.conn.execute(
        "INSERT INTO agent_keys (id, name, key_hash, key_prefix, scopes, rate_limit_rpm)"
        " VALUES ('k1', 'svc', ?, 'swarm_sec_last...', ?, 10000)",
        (hashlib.sha256(raw.encode()).hexdigest(), json.dumps(["search"])),
    )
    await db.conn.commit()
    await db.close()

    gw_server.DB_PATH = str(db_file)
    gw_server._reset_rate_limits()
    try:
        await gw_server.verify_agent_token(f"Bearer {raw}", None, "search")
        db = Database(db_file)
        await db.connect()
        async with db.conn.execute("SELECT last_used_at FROM agent_keys WHERE id = 'k1'") as cur:
            row = await cur.fetchone()
        assert row["last_used_at"] is not None
        first = row["last_used_at"]
        # Immediate re-verify is write-throttled: value unchanged.
        await gw_server.verify_agent_token(f"Bearer {raw}", None, "search")
        async with db.conn.execute("SELECT last_used_at FROM agent_keys WHERE id = 'k1'") as cur:
            row = await cur.fetchone()
        assert row["last_used_at"] == first
        await db.close()
    finally:
        gw_server._reset_rate_limits()
