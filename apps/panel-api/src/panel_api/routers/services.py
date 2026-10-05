"""Services router for managing catalog, wizard deployments, and status."""

from __future__ import annotations

import json
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from panel_api.audit import AuditLogger
from panel_api.db import Database
from panel_api.routers.auth import require_role, SessionInfo, get_db
from panel_api.swarmd_client import SwarmdClient

router = APIRouter(prefix="/services", tags=["services"])


class ServiceInstallRequest(BaseModel):
    service_id: str
    profile: str = "standard"
    params: dict[str, Any] = Field(default_factory=dict)


def get_swarmd() -> SwarmdClient:
    from panel_api.main import app_state
    return app_state.swarmd


@router.get("/catalog")
async def list_catalog(
    user: SessionInfo = Depends(require_role("admin", "operator", "viewer")),
):
    from panel_api.main import app_state
    catalog = app_state.catalog
    return [
        {
            "id": entry.id,
            "name": entry.name,
            "tier": entry.tier,
            "license": entry.license.model_dump(),
            "verified": entry.verified,
            "requires": entry.requires,
            "params_schema": entry.params_schema,
            "resources": {k: v.model_dump() for k, v in entry.resources.items()},
            "native_exposable": entry.native_exposable,
        }
        for entry in catalog.values()
    ]


@router.get("/installed")
async def list_installed(
    db: Database = Depends(get_db),
    user: SessionInfo = Depends(require_role("admin", "operator", "viewer")),
):
    async with db.conn.execute("SELECT service_id, profile, status, params, updated_at FROM installed_services") as cur:
        rows = await cur.fetchall()
        return [
            {
                "service_id": r["service_id"],
                "profile": r["profile"],
                "status": r["status"],
                "params": json.loads(r["params"]),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]


@router.post("/deploy")
async def deploy_services(
    requests: list[ServiceInstallRequest],
    db: Database = Depends(get_db),
    swarmd: SwarmdClient = Depends(get_swarmd),
    user: SessionInfo = Depends(require_role("admin", "operator")),
):
    """Renders and applies a validated stack via the swarmd sidecar."""
    wanted_payload = {
        req.service_id: {"profile": req.profile, "params": req.params}
        for req in requests
    }

    try:
        # Request compose rendering from swarmd sidecar
        res = await swarmd.send_intent("render_stack", {"wanted": wanted_payload})
        compose = res.get("compose", {})
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"swarmd rejected deployment: {e}",
        ) from e

    # Update database state for installed services
    for req in requests:
        await db.conn.execute(
            """
            INSERT OR REPLACE INTO installed_services (service_id, profile, status, params, updated_at)
            VALUES (?, ?, 'running', ?, CURRENT_TIMESTAMP)
            """,
            (req.service_id, req.profile, json.dumps(req.params)),
        )
    await db.conn.commit()

    logger = AuditLogger(db.conn)
    await logger.log(
        actor=user.username,
        action="deploy_services",
        details={"services": [req.service_id for req in requests]},
    )

    return {"ok": True, "services_count": len(compose.get("services", {}))}
