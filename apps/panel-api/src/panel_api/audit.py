"""Audit logger with cryptographic hash-chaining to ensure append-only tamper detection."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import aiosqlite


class AuditLogger:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def log(
        self,
        actor: str,
        action: str,
        target: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        # Phase 03 retry2 QA-B P0-2 defense-in-depth: scrub every target and
        # detail value so a caller that forgets sanitize_details can never
        # persist swarm_sec_*/Bearer/?token= secrets. Non-secret forensics
        # survive verbatim.
        from panel_api.ssrf_guard import sanitize_details, scrub_secrets_from_text

        if target is not None:
            target = scrub_secrets_from_text(target)
        details = sanitize_details(details or {})
        details_json = json.dumps(details, sort_keys=True)

        async with self.db.execute(
            "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            prev_hash = row["entry_hash"] if row else "GENESIS"

        # Calculate SHA-256 over prev_hash + actor + action + target + details
        h = hashlib.sha256()
        h.update(prev_hash.encode("utf-8"))
        h.update(actor.encode("utf-8"))
        h.update(action.encode("utf-8"))
        if target:
            h.update(target.encode("utf-8"))
        h.update(details_json.encode("utf-8"))
        entry_hash = h.hexdigest()

        await self.db.execute(
            """
            INSERT INTO audit_log (actor, action, target, details, prev_hash, entry_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (actor, action, target, details_json, prev_hash, entry_hash),
        )
        await self.db.commit()
        return entry_hash

    async def verify_chain(self) -> bool:
        """Verifies the cryptographic integrity of the entire audit chain.

        Ticket #10: retention pruning deletes old rows and appends an
        ``audit_checkpoint`` row recording the deleted head hash. The walk
        starts at the latest checkpoint's head (instead of GENESIS) and
        checkpoint rows are hash-checked but exempt from the prev-link check
        (their prev predates the pruned prefix by design). A leading gap with
        no covering checkpoint still fails.
        """
        query = (
            "SELECT actor, action, target, details, prev_hash, entry_hash "
            "FROM audit_log ORDER BY id ASC"
        )
        async with self.db.execute(query) as cursor:
            rows = list(await cursor.fetchall())
        head_hash = "GENESIS"
        for row in reversed(rows):
            if row["action"] == "audit_checkpoint":
                try:
                    head_hash = json.loads(row["details"]).get("head_hash") or "GENESIS"
                except Exception:
                    head_hash = "GENESIS"
                break
        expected_prev = head_hash
        for row in rows:
            if row["action"] != "audit_checkpoint" and row["prev_hash"] != expected_prev:
                return False
            h = hashlib.sha256()
            h.update(row["prev_hash"].encode("utf-8"))
            h.update(row["actor"].encode("utf-8"))
            h.update(row["action"].encode("utf-8"))
            if row["target"]:
                h.update(row["target"].encode("utf-8"))
            h.update(row["details"].encode("utf-8"))
            if h.hexdigest() != row["entry_hash"]:
                return False
            expected_prev = row["entry_hash"]
        return True

    async def prune_older_than(self, cutoff_iso: str) -> int:
        """Deletes audit rows older than *cutoff_iso*, keeping the chain
        verifiable via a checkpoint row (ticket #10).

        The horizon is the newest row older than the cutoff; every
        non-checkpoint row up to and including it is deleted (a contiguous
        id prefix — timestamps are monotonic in practice). Checkpoint rows
        are never deleted. Returns the deleted count (0 writes no checkpoint).
        """
        async with self.db.execute(
            "SELECT MAX(id) AS horizon FROM audit_log"
            " WHERE timestamp < ? AND action != 'audit_checkpoint'",
            (cutoff_iso,),
        ) as cur:
            horizon_row = await cur.fetchone()
        horizon = horizon_row["horizon"] if horizon_row else None
        if horizon is None:
            return 0
        async with self.db.execute(
            "SELECT entry_hash FROM audit_log WHERE id = ?", (horizon,)
        ) as cur:
            head = await cur.fetchone()
        head_hash = head["entry_hash"] if head else "GENESIS"
        async with self.db.execute(
            "SELECT COUNT(*) AS n FROM audit_log WHERE id <= ? AND action != 'audit_checkpoint'",
            (horizon,),
        ) as cur:
            count_row = await cur.fetchone()
            count = count_row["n"] if count_row else 0
        await self.db.execute(
            "DELETE FROM audit_log WHERE id <= ? AND action != 'audit_checkpoint'",
            (horizon,),
        )
        await self.db.commit()
        await self.log(
            actor="system",
            action="audit_checkpoint",
            details={
                "pruned_through_id": horizon,
                "head_hash": head_hash,
                "cutoff": cutoff_iso,
            },
        )
        return count
