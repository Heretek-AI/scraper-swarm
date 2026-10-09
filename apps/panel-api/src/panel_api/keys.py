"""Service-account key minting shared by the HTTP route and swarmctl (ticket #7).

The HTTP route (``routers/agents.py``) keeps its acknowledgement gates
(never-expire ack, placeholder-host ack); this module owns validation,
generation, and storage so both paths mint identical keys. Only the SHA-256
hash is ever stored; the raw bearer is returned once to the caller.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

ALLOWED_SCOPES = frozenset({"search", "scrape", "admin"})

# Default TTL mirrors the HTTP route (30 days); the CLI default matches.
DEFAULT_KEY_TTL_HOURS = 720
MAX_NAME_LENGTH = 64


class KeyExistsError(ValueError):
    """A key with that name already exists (case-insensitive)."""


def generate_raw_key() -> str:
    return f"swarm_sec_{secrets.token_hex(24)}"


def validate_scopes(scopes: list[str]) -> list[str]:
    """Dedupes (order-preserving) and fail-closed validates scopes."""
    deduped = list(dict.fromkeys(scopes))
    unknown = [s for s in deduped if s not in ALLOWED_SCOPES]
    if unknown:
        raise ValueError(f"Unknown scope(s): {unknown}. Allowed: {sorted(ALLOWED_SCOPES)}")
    if not deduped:
        raise ValueError("At least one scope is required")
    return deduped


def clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Agent key name must not be blank or whitespace-only")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ValueError("Agent key name too long (max 64)")
    return cleaned


def expiry_iso(expires_in_hours: int | None) -> str | None:
    if expires_in_hours is None:
        return None
    return (datetime.now(UTC) + timedelta(hours=expires_in_hours)).isoformat()


async def mint_key(
    conn,
    *,
    name: str,
    scopes: list[str],
    rate_limit_rpm: int = 60,
    expires_at: str | None = None,
) -> dict:
    """Inserts a key row; returns public fields plus the one-time raw bearer."""
    cleaned = clean_name(name)
    deduped = validate_scopes(list(scopes))
    key_id = secrets.token_hex(16)
    raw_key = generate_raw_key()
    key_prefix = raw_key[:14] + "..."
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    try:
        await conn.execute(
            """
            INSERT INTO agent_keys
                (id, name, key_hash, key_prefix, scopes, rate_limit_rpm, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key_id,
                cleaned,
                key_hash,
                key_prefix,
                json.dumps(deduped),
                rate_limit_rpm,
                expires_at,
            ),
        )
        await conn.commit()
    except sqlite3.IntegrityError as e:
        raise KeyExistsError(f"Agent key name '{cleaned}' already exists") from e
    return {
        "id": key_id,
        "name": cleaned,
        "raw_key": raw_key,
        "key_prefix": key_prefix,
        "scopes": deduped,
        "rate_limit_rpm": rate_limit_rpm,
        "expires_at": expires_at,
    }


async def find_key(conn, ref: str):
    """Finds a key row by id or (case-insensitive) name."""
    async with conn.execute(
        "SELECT id, name, key_hash, key_prefix, scopes, rate_limit_rpm,"
        " created_at, expires_at FROM agent_keys WHERE id = ? OR name = ? COLLATE NOCASE",
        (ref, ref),
    ) as cur:
        return await cur.fetchone()


async def rotate_key(
    conn,
    *,
    ref: str,
    grace_hours: float = 24,
    expires_in_hours: int | None = DEFAULT_KEY_TTL_HOURS,
) -> dict:
    """Rotates a key: the old row is renamed and expires after the grace
    period (stays valid until then); a new row takes the original name with
    the same scopes and rate limit. Returns {"old": ..., "new": ...}."""
    row = await find_key(conn, ref)
    if row is None:
        raise KeyError(f"Key '{ref}' not found")
    old_name = row["name"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    retired_name = f"{old_name} (retired {stamp})"
    grace_expires = (datetime.now(UTC) + timedelta(hours=grace_hours)).isoformat()
    await conn.execute(
        "UPDATE agent_keys SET name = ?, expires_at = ? WHERE id = ?",
        (retired_name, grace_expires, row["id"]),
    )
    await conn.commit()
    new = await mint_key(
        conn,
        name=old_name,
        scopes=json.loads(row["scopes"]),
        rate_limit_rpm=int(row["rate_limit_rpm"] or 60),
        expires_at=expiry_iso(expires_in_hours),
    )
    return {
        "old": {"id": row["id"], "name": retired_name, "expires_at": grace_expires},
        "new": new,
    }
