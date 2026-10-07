"""Database schema and connection manager using aiosqlite."""

from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'operator', 'viewer')),
    totp_secret TEXT, -- encrypted via Vault
    totp_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    credential_data TEXT NOT NULL, -- JSON-serialized public key / credential record
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_keys (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    key_hash TEXT UNIQUE NOT NULL, -- SHA-256 hash of the bearer token
    key_prefix TEXT NOT NULL, -- First 14 chars for user display (raw_key[:14] + "...")
    scopes TEXT NOT NULL,     -- JSON array of scopes, e.g. ["search", "scrape"]
    rate_limit_rpm INTEGER DEFAULT 60,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS installed_services (
    service_id TEXT PRIMARY KEY,
    profile TEXT NOT NULL DEFAULT 'standard',
    status TEXT NOT NULL DEFAULT 'stopped',
    params TEXT NOT NULL DEFAULT '{}', -- JSON dictionary
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    details TEXT NOT NULL DEFAULT '{}', -- JSON dictionary
    prev_hash TEXT,
    entry_hash TEXT NOT NULL
);
"""


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure strict file permissions on database file
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        # Phase 03 retry2 QA-B P0-1/P0-5 migrations for legacy DBs (no 500):
        # - sessions.expires_at column may be missing -> ADD COLUMN
        # - legacy NULL/''/whitespace sessions swept to expired (fail-closed)
        # - agent_keys.name UNIQUE NOCASE index for dup-name race + squatting
        await self._migrate_legacy_schema()

        if self.db_path.exists():
            os.chmod(self.db_path, 0o600)

    async def _migrate_legacy_schema(self) -> None:
        """Best-effort legacy migrations; never raises (connect must succeed)."""
        import logging

        _log = logging.getLogger(__name__)
        if self._conn is None:
            return
        try:
            async with self._conn.execute("PRAGMA table_info(sessions)") as cur:
                sess_cols = [r["name"] for r in await cur.fetchall()]
            if sess_cols and "expires_at" not in sess_cols:
                await self._conn.execute("ALTER TABLE sessions ADD COLUMN expires_at TIMESTAMP")
                await self._conn.commit()
                async with self._conn.execute("PRAGMA table_info(sessions)") as cur:
                    sess_cols = [r["name"] for r in await cur.fetchall()]
            if "expires_at" in sess_cols:
                await self._conn.execute(
                    "UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'"
                    " WHERE expires_at IS NULL OR TRIM(expires_at) = ''"
                )
                await self._conn.commit()
        except Exception as e:
            _log.debug("Legacy session migration skipped: %s", e)
        try:
            await self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_keys_name_nocase"
                " ON agent_keys(name COLLATE NOCASE)"
            )
            await self._conn.commit()
        except Exception as e:
            # Pre-existing case-variant duplicates: leave rows untouched; the
            # per-request NOCASE SELECT + IntegrityError path still 409s new
            # conflicts without breaking connect on old DBs.
            _log.debug("Agent-key NOCASE index skipped: %s", e)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Database connection is not open.")
        return self._conn
