"""Agent connections router for managing scoped API keys and OpenCode configurations.

Phase 03-opencode-integration (P2 OpenCode v2 live integration):
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P2-C1-C2-C3
- file:///home/john/Projects/scraper-swarm/apps/panel-api/src/panel_api/routers/agents.py
- file:///home/john/Projects/scraper-swarm/apps/gateway/src/gateway/server.py
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from panel_api.audit import AuditLogger
from panel_api.db import Database
from panel_api.routers.auth import SessionInfo, get_db, require_role
from pydantic import BaseModel, Field

router = APIRouter(prefix="/agents", tags=["agents"])

# Phase 03: allowlist for per-tool gateway scopes. Unknown scopes are rejected
# fail-closed so a typo can never mint an over-privileged key.
ALLOWED_SCOPES = frozenset({"search", "scrape", "admin"})

# Phase 03: public MCP URL template for the one-time opencode.json snippet.
# Operators override SWARM_PUBLIC_MCP_URL to their real gateway origin.
# NOTE (retry1 QA-B P0-5): the URL is resolved at request time (see
# _resolve_public_mcp_url), never bound at import; a missing/unconfigured host
# yields a conspicuous REPLACE-ME placeholder plus a warning so a live Bearer
# is never pasted beside a dead host without noticing.
SWARM_PUBLIC_MCP_URL = os.environ.get("SWARM_PUBLIC_MCP_URL", "https://swarm.example.com/mcp")

# Default TTL when the caller omits expires_in_hours (30 days). Infinite keys
# require explicit opt-in via allow_never_expire (retry1 QA-B P0-5).
DEFAULT_KEY_TTL_HOURS = 720


def _resolve_public_mcp_url() -> tuple[str, bool]:
    """Reads SWARM_PUBLIC_MCP_URL at request time; reports placeholder status.

    Returns (url, is_placeholder). Placeholder hosts (unset env, example.com,
    REPLACE-ME) are rewritten to a conspicuous https://REPLACE-ME.invalid/mcp
    so the snippet cannot be mistaken for a working endpoint.
    """
    raw = os.environ.get("SWARM_PUBLIC_MCP_URL", "").strip()
    if not raw:
        return "https://REPLACE-ME.invalid/mcp", True
    lowered = raw.lower()
    if "example.com" in lowered or "replace-me" in lowered or "invalid" in lowered:
        return "https://REPLACE-ME.invalid/mcp", True
    return raw, False


class CreateAgentKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scopes: list[str] = Field(default=["search", "scrape"])
    rate_limit_rpm: int = Field(default=60, ge=1, le=1000)
    # Phase 03: optional TTL in hours; stored as expires_at for gateway 401 enforcement.
    # Retry1: defaults to 30d; pass expires_in_hours=None + allow_never_expire=True
    # for an explicitly-acknowledged never-expiring key.
    expires_in_hours: int | None = Field(default=DEFAULT_KEY_TTL_HOURS, ge=1, le=8760)
    allow_never_expire: bool = Field(default=False)


class CreateAgentKeyResponse(BaseModel):
    id: str
    name: str
    raw_key: str  # Presented ONCE to user
    key_prefix: str
    scopes: list[str]
    rate_limit_rpm: int
    expires_at: str | None = None
    opencode_snippet: dict


@router.post("/keys", response_model=CreateAgentKeyResponse)
async def create_agent_key(
    req: CreateAgentKeyRequest,
    db: Database = Depends(get_db),
    user: SessionInfo = Depends(require_role("admin", "operator")),
):
    # Phase 03 retry1 QA-B P0-5: dedupe scopes (preserve order) before validation.
    deduped_scopes: list[str] = list(dict.fromkeys(req.scopes))

    # Phase 03: fail-closed scope validation (AC5 scope enforcement starts at issuance).
    unknown = [s for s in deduped_scopes if s not in ALLOWED_SCOPES]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown scope(s): {unknown}. Allowed: {sorted(ALLOWED_SCOPES)}",
        )
    if not deduped_scopes:
        raise HTTPException(status_code=422, detail="At least one scope is required")

    # Phase 03 retry1 QA-B P0-5: duplicate key names rejected with 409 so
    # operators cannot mint ambiguous keys.
    async with db.conn.execute(
        "SELECT id FROM agent_keys WHERE name = ?", (req.name,)
    ) as cur:
        if await cur.fetchone():
            raise HTTPException(
                status_code=409, detail=f"Agent key name '{req.name}' already exists"
            )

    # Phase 03 retry1 QA-B P0-5: infinite keys require explicit acknowledgement.
    if req.expires_in_hours is None and not req.allow_never_expire:
        raise HTTPException(
            status_code=422,
            detail="expires_in_hours=None creates a never-expiring key; "
            "pass allow_never_expire=true to acknowledge or set a TTL",
        )

    key_id = secrets.token_hex(16)
    # Generate high-entropy bearer token: swarm_sec_<32 hex>
    raw_key = f"swarm_sec_{secrets.token_hex(24)}"
    key_prefix = raw_key[:14] + "..."
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    scopes_json = json.dumps(deduped_scopes)

    expires_at: str | None = None
    if req.expires_in_hours is not None:
        expires_at = (datetime.now(UTC) + timedelta(hours=req.expires_in_hours)).isoformat()

    await db.conn.execute(
        """
        INSERT INTO agent_keys (id, name, key_hash, key_prefix, scopes, rate_limit_rpm, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (key_id, req.name, key_hash, key_prefix, scopes_json, req.rate_limit_rpm, expires_at),
    )
    await db.conn.commit()

    logger = AuditLogger(db.conn)
    await logger.log(
        actor=user.username,
        action="create_agent_key",
        target=key_id,
        # Phase 03: never log raw_key/key_hash — prefix + scopes only (no extra leakage).
        details={"name": req.name, "scopes": deduped_scopes, "key_prefix": key_prefix},
    )

    # Phase 03 retry1 QA-B P0-5: resolve host at request time; flag placeholders.
    public_url, is_placeholder = _resolve_public_mcp_url()
    opencode_snippet: dict = {
        "$schema": "https://opencode.ai/config.json",
        "plugin": ["@scraper-swarm/opencode-plugin"],
        "mcp": {
            "scraper-swarm": {
                "type": "remote",
                "url": public_url,
                "headers": {"Authorization": f"Bearer {raw_key}"},
                "enabled": True,
            }
        },
    }
    if is_placeholder:
        opencode_snippet["warning"] = (
            "SWARM_PUBLIC_MCP_URL is not configured: snippet host is a "
            "REPLACE-ME placeholder. Set SWARM_PUBLIC_MCP_URL to the real "
            "gateway origin before sharing this snippet."
        )

    return CreateAgentKeyResponse(
        id=key_id,
        name=req.name,
        raw_key=raw_key,
        key_prefix=key_prefix,
        scopes=deduped_scopes,
        rate_limit_rpm=req.rate_limit_rpm,
        expires_at=expires_at,
        opencode_snippet=opencode_snippet,
    )


@router.get("/keys")
async def list_agent_keys(
    db: Database = Depends(get_db),
    user: SessionInfo = Depends(require_role("admin", "operator", "viewer")),
):
    query = (
        "SELECT id, name, key_prefix, scopes, rate_limit_rpm, created_at, expires_at "
        "FROM agent_keys ORDER BY created_at DESC"
    )
    async with db.conn.execute(query) as cur:
        rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "key_prefix": r["key_prefix"],
                "scopes": json.loads(r["scopes"]),
                "rate_limit_rpm": r["rate_limit_rpm"],
                "created_at": r["created_at"],
                "expires_at": r["expires_at"],
            }
            for r in rows
        ]


@router.delete("/keys/{key_id}")
async def revoke_agent_key(
    key_id: str,
    db: Database = Depends(get_db),
    user: SessionInfo = Depends(require_role("admin", "operator")),
):
    res = await db.conn.execute("DELETE FROM agent_keys WHERE id = ?", (key_id,))
    await db.conn.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Key not found")

    logger = AuditLogger(db.conn)
    await logger.log(actor=user.username, action="revoke_agent_key", target=key_id)
    return {"ok": True}
