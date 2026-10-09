"""Ticket #10: audit retention with verifiable checkpoint rows."""

from __future__ import annotations

from pathlib import Path

import pytest
from panel_api.audit import AuditLogger
from panel_api.db import Database


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "panel.db")
    await database.connect()
    yield database
    await database.close()


async def _log(logger: AuditLogger, action: str, target: str | None = None) -> str:
    return await logger.log(actor="tester", action=action, target=target, details={"n": action})


@pytest.mark.asyncio
async def test_prune_keeps_chain_verifiable(db):
    logger = AuditLogger(db.conn)
    await _log(logger, "a1", "old-one")
    await _log(logger, "a2", "old-two")
    await _log(logger, "a3", "fresh")
    async with db.conn.execute("SELECT id, entry_hash FROM audit_log ORDER BY id ASC") as cur:
        rows = await cur.fetchall()
    old_head = rows[1]["entry_hash"]
    await db.conn.execute(
        "UPDATE audit_log SET timestamp = '2000-01-01T00:00:00+00:00' WHERE id IN (?, ?)",
        (rows[0]["id"], rows[1]["id"]),
    )
    await db.conn.execute(
        "UPDATE audit_log SET timestamp = '2026-10-08T00:00:00+00:00' WHERE id = ?",
        (rows[2]["id"],),
    )
    await db.conn.commit()

    pruned = await logger.prune_older_than("2026-10-01T00:00:00+00:00")
    assert pruned == 2
    async with db.conn.execute(
        "SELECT actor, action, target FROM audit_log ORDER BY id ASC"
    ) as cur:
        remaining = await cur.fetchall()
    assert [r["action"] for r in remaining] == ["a3", "audit_checkpoint"]
    assert all("old-" not in (r["target"] or "") for r in remaining)
    async with db.conn.execute(
        "SELECT details, prev_hash FROM audit_log WHERE action = 'audit_checkpoint'"
    ) as cur:
        checkpoint = await cur.fetchone()
    import json as _json

    details = _json.loads(checkpoint["details"])
    assert details["head_hash"] == old_head
    # The checkpoint chains AFTER the remaining rows (prev = a3's hash); the
    # deleted prefix is covered by details.head_hash, which verify_chain uses
    # as the walk start.
    async with db.conn.execute("SELECT entry_hash FROM audit_log WHERE action = 'a3'") as cur:
        a3_hash = (await cur.fetchone())["entry_hash"]
    assert checkpoint["prev_hash"] == a3_hash
    assert await logger.verify_chain() is True


@pytest.mark.asyncio
async def test_prune_with_nothing_old_writes_no_checkpoint(db):
    logger = AuditLogger(db.conn)
    await _log(logger, "fresh", "x")
    pruned = await logger.prune_older_than("2000-01-01T00:00:00+00:00")
    assert pruned == 0
    async with db.conn.execute("SELECT COUNT(*) AS n FROM audit_log") as cur:
        assert (await cur.fetchone())["n"] == 1
    assert await logger.verify_chain() is True


@pytest.mark.asyncio
async def test_chain_without_checkpoints_still_genesis_verified(db):
    logger = AuditLogger(db.conn)
    await _log(logger, "a", "x")
    assert await logger.verify_chain() is True


@pytest.mark.asyncio
async def test_swarmctl_audit_prune(tmp_path: Path):
    from panel_api.swarmctl import amain as swarmctl_amain

    db_file = tmp_path / "panel.db"
    database = Database(db_file)
    await database.connect()
    logger = AuditLogger(database.conn)
    await _log(logger, "oldie", "gone")
    await database.conn.execute("UPDATE audit_log SET timestamp = '2000-01-01T00:00:00+00:00'")
    await database.conn.commit()
    await database.close()

    rc = await swarmctl_amain(["--db", str(db_file), "audit", "prune", "--retention-days", "30"])
    assert rc == 0
    check = Database(db_file)
    await check.connect()
    assert await AuditLogger(check.conn).verify_chain() is True
    async with check.conn.execute("SELECT action FROM audit_log") as cur:
        actions = [r["action"] for r in await cur.fetchall()]
    assert "oldie" not in actions
    assert "audit_checkpoint" in actions
    await check.close()


@pytest.mark.asyncio
async def test_tamper_after_prune_still_detected(db):
    """QA: modifying a post-prune row breaks verification."""
    logger = AuditLogger(db.conn)
    await _log(logger, "old", "gone")
    await _log(logger, "new", "kept")
    await db.conn.execute(
        "UPDATE audit_log SET timestamp = '2000-01-01T00:00:00+00:00' WHERE action = 'old'"
    )
    await db.conn.commit()
    assert await logger.prune_older_than("2026-10-01T00:00:00+00:00") == 1
    assert await logger.verify_chain() is True
    await db.conn.execute("UPDATE audit_log SET target = 'forged' WHERE action = 'new'")
    await db.conn.commit()
    assert await logger.verify_chain() is False


@pytest.mark.asyncio
async def test_double_prune_keeps_chain_verifiable(db):
    """Defect 3: two successive prunes spanning a checkpoint must verify."""
    logger = AuditLogger(db.conn)
    await _log(logger, "a1", "a1")
    await _log(logger, "a2", "a2")
    await _log(logger, "a3", "a3")
    async with db.conn.execute("SELECT id, action FROM audit_log ORDER BY id ASC") as cur:
        rows = await cur.fetchall()
    ids = [r["id"] for r in rows]
    await db.conn.execute(
        "UPDATE audit_log SET timestamp = '2000-01-01T00:00:00+00:00' WHERE id IN (?, ?)",
        (ids[0], ids[1]),
    )
    await db.conn.execute(
        "UPDATE audit_log SET timestamp = '2026-10-08T00:00:00+00:00' WHERE id = ?",
        (ids[2],),
    )
    await db.conn.commit()
    assert await logger.prune_older_than("2026-10-01T00:00:00+00:00") == 2
    assert await logger.verify_chain() is True
    await _log(logger, "a4", "a4")
    await _log(logger, "a5", "a5")
    async with db.conn.execute("SELECT id FROM audit_log WHERE action IN ('a3', 'a4')") as cur:
        old_ids = [r["id"] for r in await cur.fetchall()]
    assert len(old_ids) == 2
    await db.conn.execute(
        "UPDATE audit_log SET timestamp = '2000-01-01T00:00:00+00:00' WHERE id IN (?, ?)",
        tuple(old_ids),
    )
    await db.conn.commit()
    assert await logger.prune_older_than("2026-10-01T00:00:00+00:00") == 2
    assert await logger.verify_chain() is True
