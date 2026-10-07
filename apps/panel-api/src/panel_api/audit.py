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
        """Verifies the cryptographic integrity of the entire audit chain."""
        query = (
            "SELECT actor, action, target, details, prev_hash, entry_hash "
            "FROM audit_log ORDER BY id ASC"
        )
        async with self.db.execute(query) as cursor:
            expected_prev = "GENESIS"
            async for row in cursor:
                if row["prev_hash"] != expected_prev:
                    return False
                h = hashlib.sha256()
                h.update(expected_prev.encode("utf-8"))
                h.update(row["actor"].encode("utf-8"))
                h.update(row["action"].encode("utf-8"))
                if row["target"]:
                    h.update(row["target"].encode("utf-8"))
                h.update(row["details"].encode("utf-8"))
                if h.hexdigest() != row["entry_hash"]:
                    return False
                expected_prev = row["entry_hash"]
        return True
