"""Agent connections router for managing scoped API keys and OpenCode configurations."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from panel_api.audit import AuditLogger
from panel_api.db import Database
from panel_api.routers.auth import require_role, SessionInfo, get_db

router = APIRouter(prefix="/agents", tags=["agents"])


class CreateAgentKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scopes: list[str] = Field(default=["search", "scrape"])
    rate_limit_rpm: int = Field(default=60, ge=1, le=1000)


class CreateAgentKeyResponse(BaseModel):
    id: str
    name: str
    raw_key: str  # Presented ONCE to user
    key_prefix: str
    scopes: list[str]
    rate_limit_rpm: int
    opencode_snippet: dict


@router.post("/keys", response_model=CreateAgentKeyResponse)
async def create_agent_key(
    req: CreateAgentKeyRequest,
    db: Database = Depends(get_db),
    user: SessionInfo = Depends(require_role("admin", "operator")),
):
    key_id = secrets.token_hex(16)
    # Generate high-entropy bearer token: swarm_sec_<32 hex>
    raw_key = f"swarm_sec_{secrets.token_hex(24)}"
    key_prefix = raw_key[:14] + "..."
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    scopes_json = json.dumps(req.scopes)

    await db.conn.execute(
        """
        INSERT INTO agent_keys (id, name, key_hash, key_prefix, scopes, rate_limit_rpm)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (key_id, req.name, key_hash, key_prefix, scopes_json, req.rate_limit_rpm),
    )
    await db.conn.commit()

    logger = AuditLogger(db.conn)
    await logger.log(
        actor=user.username,
        action="create_agent_key",
        target=key_id,
        details={"name": req.name, "scopes": req.scopes},
    )

    opencode_snippet = {
        "$schema": "https://opencode.ai/config.json",
        "plugin": ["@scraper-swarm/opencode-plugin"],
        "mcp": {
            "scraper-swarm": {
                "type": "remote",
                "url": "https://swarm.example.com/mcp",
                "headers": {
                    "Authorization": f"Bearer {raw_key}"
                },
                "enabled": True
            }
        }
    }

    return CreateAgentKeyResponse(
        id=key_id,
        name=req.name,
        raw_key=raw_key,
        key_prefix=key_prefix,
        scopes=req.scopes,
        rate_limit_rpm=req.rate_limit_rpm,
        opencode_snippet=opencode_snippet,
    )


@router.get("/keys")
async def list_agent_keys(
    db: Database = Depends(get_db),
    user: SessionInfo = Depends(require_role("admin", "operator", "viewer")),
):
    async with db.conn.execute(
        "SELECT id, name, key_prefix, scopes, rate_limit_rpm, created_at FROM agent_keys ORDER BY created_at DESC"
    ) as cur:
        rows = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "key_prefix": r["key_prefix"],
                "scopes": json.loads(r["scopes"]),
                "rate_limit_rpm": r["rate_limit_rpm"],
                "created_at": r["created_at"],
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
