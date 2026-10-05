"""Services router for managing catalog, wizard deployments, and status."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from panel_api.audit import AuditLogger
from panel_api.db import Database
from panel_api.routers.auth import SessionInfo, get_db, require_role
from panel_api.swarmd_client import SwarmdClient
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/services", tags=["services"])


class ServiceInstallRequest(BaseModel):
    service_id: str
    profile: str = "standard"
    params: dict[str, Any] = Field(default_factory=dict)


def get_swarmd() -> SwarmdClient:
    from panel_api.main import app_state
    return app_state.swarmd


@router.get("/catalog")
async def list_catalog():
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
    query = (
        "SELECT service_id, profile, status, params, updated_at "
        "FROM installed_services"
    )
    async with db.conn.execute(query) as cur:
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
    # Merge with currently active installed services
    query = (
        "SELECT service_id, profile, params FROM installed_services "
        "WHERE status != 'stopped'"
    )
    async with db.conn.execute(query) as cur:
        rows = await cur.fetchall()
        wanted_payload = {
            r["service_id"]: {"profile": r["profile"], "params": json.loads(r["params"] or "{}")}
            for r in rows
        }

    for req in requests:
        wanted_payload[req.service_id] = {"profile": req.profile, "params": req.params}

    try:
        # Request live compose application from swarmd sidecar
        res = await swarmd.send_intent("apply_stack", {"wanted": wanted_payload})
        compose = res.get("compose", {})
        output = res.get("output", "")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"swarmd rejected deployment: {e}",
        ) from e

    # Update database state for installed services
    for req in requests:
        await db.conn.execute(
            """
            INSERT OR REPLACE INTO installed_services
            (service_id, profile, status, params, updated_at)
            VALUES (?, ?, 'running', ?, CURRENT_TIMESTAMP)
            """,
            (req.service_id, req.profile, json.dumps(req.params)),
        )
    await db.conn.commit()

    audit_logger = AuditLogger(db.conn)
    await audit_logger.log(
        actor=user.username,
        action="deploy_services",
        details={"services": [req.service_id for req in requests]},
    )

    return {"ok": True, "services_count": len(compose.get("services", {})), "output": output}


@router.get("/status")
async def get_live_services_status(
    db: Database = Depends(get_db),
    swarmd: SwarmdClient = Depends(get_swarmd),
    user: SessionInfo = Depends(require_role("admin", "operator", "viewer")),
):
    """Returns live Docker container execution status queried through swarmd sidecar."""
    containers: list[dict[str, Any]] = []
    try:
        res = await swarmd.send_intent("get_ps", {})
        containers = res.get("containers", [])
    except Exception as e:
        logger.debug("Failed to query get_ps from swarmd: %s", e)

    query = (
        "SELECT service_id, profile, status, params, updated_at "
        "FROM installed_services"
    )
    async with db.conn.execute(query) as cur:
        rows = await cur.fetchall()
        installed_map = {r["service_id"]: dict(r) for r in rows}

    return {
        "containers": containers,
        "installed": installed_map,
    }


@router.get("/compose")
async def get_active_compose(
    swarmd: SwarmdClient = Depends(get_swarmd),
    user: SessionInfo = Depends(require_role("admin", "operator", "viewer")),
):
    """Returns the current active rendered docker-compose.yml YAML string."""
    try:
        res = await swarmd.send_intent("get_compose", {})
        return {"compose_yaml": res.get("compose_yaml", "")}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch active compose: {e}",
        ) from e


@router.get("/{service_id}/logs")
async def get_service_logs(
    service_id: str,
    lines: int = 100,
    swarmd: SwarmdClient = Depends(get_swarmd),
    user: SessionInfo = Depends(require_role("admin", "operator", "viewer")),
):
    """Fetches real container logs from swarmd."""
    try:
        res = await swarmd.send_intent("get_logs", {"service": service_id, "lines": lines})
        return {"service": service_id, "logs": res.get("logs", "")}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to fetch logs: {e}",
        ) from e


@router.post("/down")
async def tear_down_services(
    db: Database = Depends(get_db),
    swarmd: SwarmdClient = Depends(get_swarmd),
    user: SessionInfo = Depends(require_role("admin", "operator")),
):
    """Tears down all running engines via swarmd sidecar."""
    try:
        res = await swarmd.send_intent("down_stack", {})
        output = res.get("output", "")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    query = (
        "UPDATE installed_services SET status = 'stopped', updated_at = CURRENT_TIMESTAMP"
    )
    await db.conn.execute(query)
    await db.conn.commit()

    audit_logger = AuditLogger(db.conn)
    await audit_logger.log(actor=user.username, action="down_stack", details={"output": output})

    return {"ok": True, "output": output}


@router.post("/{service_id}/test")
async def test_service(
    service_id: str,
    user: SessionInfo = Depends(require_role("admin", "operator", "viewer")),
):
    """Runs automated diagnostics and smoke test against a running engine container."""
    from panel_api.smoke_test import SmokeTestRunner
    result = await SmokeTestRunner.run(service_id)
    return result


@router.post("/test-all")
async def test_all_services(
    db: Database = Depends(get_db),
    user: SessionInfo = Depends(require_role("admin", "operator", "viewer")),
):
    """Runs automated smoke tests across all currently running engines."""
    from panel_api.smoke_test import SmokeTestRunner
    query = "SELECT service_id FROM installed_services WHERE status != 'stopped'"
    async with db.conn.execute(query) as cur:
        rows = await cur.fetchall()
        services = [r["service_id"] for r in rows]

    results = []
    for sid in services:
        results.append(await SmokeTestRunner.run(sid))
    # Always include egress security test
    results.append(await SmokeTestRunner.run("egress-guard"))

    return {
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "results": results,
    }


@router.post("/{service_id}/restart")
async def restart_service(
    service_id: str,
    db: Database = Depends(get_db),
    swarmd: SwarmdClient = Depends(get_swarmd),
    user: SessionInfo = Depends(require_role("admin", "operator")),
):
    """Re-applies the service configuration to restart the container."""
    query = "SELECT profile, params FROM installed_services WHERE service_id = ?"
    async with db.conn.execute(query, (service_id,)) as cur:
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Service not installed")
        profile = row["profile"]
        params = json.loads(row["params"] or "{}")

    # Merge active services and re-apply
    query = (
        "SELECT service_id, profile, params FROM installed_services "
        "WHERE status != 'stopped'"
    )
    async with db.conn.execute(query) as cur:
        rows = await cur.fetchall()
        wanted_payload = {
            r["service_id"]: {"profile": r["profile"], "params": json.loads(r["params"] or "{}")}
            for r in rows
        }
    wanted_payload[service_id] = {"profile": profile, "params": params}

    res = await swarmd.send_intent("apply_stack", {"wanted": wanted_payload})
    return {"ok": True, "output": res.get("output", "")}

