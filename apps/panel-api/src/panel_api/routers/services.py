"""Services router for managing catalog, wizard deployments, and status.

Phase 02-infra-reconcile (evidence:
file:///home/john/Projects/scraper-swarm/.roadmap/01-live-smoke/dossier.json):
GET /services/status joins DB ``installed_services`` against live Docker
``ps`` so DB-vs-Docker drift is visible (01 escalated with DB ``running``
vs 0 containers and ``test-all 1/3``); on drift it triggers a best-effort
background ``reconcile_now`` repair and otherwise marks services degraded
with a reason. Status/repair payloads carry names, states, and latencies
only -- never secrets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from panel_api.audit import AuditLogger
from panel_api.db import Database
from panel_api.routers.auth import SessionInfo, get_db, require_role
from panel_api.swarmd_client import SwarmdClient
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/services", tags=["services"])

# Minimum seconds between automatic background repairs triggered by
# GET /services/status (prevents repair storms when the UI polls status).
_AUTO_REPAIR_COOLDOWN_S = 120.0
_last_auto_repair_ts: float = 0.0
_auto_repair_task: asyncio.Task | None = None


class ServiceInstallRequest(BaseModel):
    service_id: str
    profile: str = "standard"
    params: dict[str, Any] = Field(default_factory=dict)


def get_swarmd() -> SwarmdClient:
    from panel_api.main import app_state

    return app_state.swarmd


def _live_service_names(containers: list[dict[str, Any]]) -> set[str]:
    """Service ids with a ``running`` container row.

    ``docker compose ps --format json`` rows carry ``Service`` + ``State``;
    the ``Name`` fallback strips the ``scraper-swarm-`` project prefix for
    older/foreign rows.
    """
    running: set[str] = set()
    for c in containers:
        state = str(c.get("State") or "").lower()
        if state != "running":
            continue
        svc = str(c.get("Service") or "")
        if not svc:
            name = str(c.get("Name") or c.get("Names") or "").strip().lstrip("/")
            if name.startswith("scraper-swarm-"):
                name = name.removeprefix("scraper-swarm-")
                if name.endswith("-1"):
                    name = name[: -len("-1")]
            svc = name
        if svc:
            running.add(svc)
    return running


def compute_drift(installed_running: list[str], containers: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure drift join: DB ``running`` rows vs live running containers.

    Returns ``{"running": [...], "drift": [...], "degraded": [...]}`` where
    each degraded entry is ``{"service_id": sid, "reason": ...}``.
    """
    live = _live_service_names(containers)
    drift = sorted(set(installed_running) - live)
    degraded = [
        {
            "service_id": sid,
            "reason": (
                "no live container for DB 'running' row "
                f"(expected scraper-swarm-{sid} running); "
                "auto-repair attempted or suspended -- see reconcile"
            ),
        }
        for sid in drift
    ]
    return {"running": sorted(live), "drift": drift, "degraded": degraded}


async def _run_background_repair(swarmd: SwarmdClient) -> None:
    try:
        report = await asyncio.wait_for(swarmd.send_intent("reconcile_now", {}), timeout=180.0)
        logger.info("background auto-repair finished: %s", report.get("reconcile"))
    except Exception as e:
        logger.warning("background auto-repair failed: %s", e)


def _maybe_trigger_auto_repair(swarmd: SwarmdClient, drift: list[str]) -> bool:
    """Fires one background ``reconcile_now`` per cooldown window."""
    global _last_auto_repair_ts, _auto_repair_task
    if not drift:
        return False
    now = time.monotonic()
    if now - _last_auto_repair_ts < _AUTO_REPAIR_COOLDOWN_S:
        return False
    if _auto_repair_task is not None and not _auto_repair_task.done():
        return False
    _last_auto_repair_ts = now
    _auto_repair_task = asyncio.create_task(_run_background_repair(swarmd))
    return True


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
    query = "SELECT service_id, profile, status, params, updated_at FROM installed_services"
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
    query = "SELECT service_id, profile, params FROM installed_services WHERE status != 'stopped'"
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
    """Returns live Docker container execution status queried through swarmd sidecar.

    Phase 02-infra-reconcile: joins DB ``installed_services`` against live
    ``ps`` so drift is explicit (``drift`` + ``degraded`` with reason). On
    drift a background ``reconcile_now`` repair is triggered (at most one
    per cooldown window); the swarmd loop also self-heals on its interval.
    """
    containers: list[dict[str, Any]] = []
    try:
        res = await swarmd.send_intent("get_ps", {})
        containers = res.get("containers", [])
    except Exception as e:
        logger.debug("Failed to query get_ps from swarmd: %s", e)

    query = "SELECT service_id, profile, status, params, updated_at FROM installed_services"
    async with db.conn.execute(query) as cur:
        rows = await cur.fetchall()
        installed_map = {r["service_id"]: dict(r) for r in rows}

    installed_running = [
        sid for sid, row in installed_map.items() if row.get("status") == "running"
    ]
    drift_info = compute_drift(installed_running, containers)

    reconcile: dict[str, Any] | None = None
    try:
        res = await swarmd.send_intent("get_reconcile_status", {})
        reconcile = res.get("reconcile")
    except Exception as e:
        logger.debug("Failed to query get_reconcile_status from swarmd: %s", e)

    repair_triggered = _maybe_trigger_auto_repair(swarmd, drift_info["drift"])

    return {
        "containers": containers,
        "installed": installed_map,
        "running": drift_info["running"],
        "drift": drift_info["drift"],
        "degraded": drift_info["degraded"],
        "reconcile": reconcile,
        "repair_triggered": repair_triggered,
    }


@router.get("/reconcile")
async def get_reconcile_status(
    swarmd: SwarmdClient = Depends(get_swarmd),
    user: SessionInfo = Depends(require_role("admin", "operator", "viewer")),
):
    """Operator inspect: reconciler desired-state, interval, and last pass.

    Phase 02-infra-reconcile (evidence:
    file:///home/john/Projects/scraper-swarm/.roadmap/01-live-smoke/dossier.json).
    Read-only; triggers no repair.
    """
    try:
        res = await swarmd.send_intent("get_reconcile_status", {})
        return res.get("reconcile", {})
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"swarmd reconcile status unavailable: {e}",
        ) from e


@router.post("/reconcile")
async def run_reconcile_now(
    db: Database = Depends(get_db),
    swarmd: SwarmdClient = Depends(get_swarmd),
    user: SessionInfo = Depends(require_role("admin", "operator")),
):
    """On-demand drift repair: runs one reconcile pass, then re-verifies.

    Phase 02-infra-reconcile: returns the reconcile report plus post-repair
    drift so ``test-all 3/3`` reproducibility can be restored on demand
    without a full re-deploy. May take up to ~3 minutes on image pulls.
    """
    try:
        res = await asyncio.wait_for(swarmd.send_intent("reconcile_now", {}), timeout=180.0)
    except TimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="reconcile timed out after 180s; check swarmd logs and retry",
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"swarmd reconcile failed: {e}",
        ) from e
    report = res.get("reconcile", {})

    containers: list[dict[str, Any]] = []
    try:
        ps = await swarmd.send_intent("get_ps", {})
        containers = ps.get("containers", [])
    except Exception as e:
        logger.debug("post-reconcile get_ps failed: %s", e)

    query = "SELECT service_id FROM installed_services WHERE status = 'running'"
    async with db.conn.execute(query) as cur:
        rows = await cur.fetchall()
        installed_running = [r["service_id"] for r in rows]
    drift_info = compute_drift(installed_running, containers)

    audit_logger = AuditLogger(db.conn)
    await audit_logger.log(
        actor=user.username,
        action="reconcile_now",
        details={
            "action": report.get("action"),
            "repaired": report.get("repaired", []),
            "still_missing": report.get("still_missing", []),
        },
    )

    return {
        "reconcile": report,
        "drift_after": drift_info["drift"],
        "degraded": drift_info["degraded"],
        "running": drift_info["running"],
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

    query = "UPDATE installed_services SET status = 'stopped', updated_at = CURRENT_TIMESTAMP"
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
    query = "SELECT service_id, profile, params FROM installed_services WHERE status != 'stopped'"
    async with db.conn.execute(query) as cur:
        rows = await cur.fetchall()
        wanted_payload = {
            r["service_id"]: {"profile": r["profile"], "params": json.loads(r["params"] or "{}")}
            for r in rows
        }
    wanted_payload[service_id] = {"profile": profile, "params": params}

    res = await swarmd.send_intent("apply_stack", {"wanted": wanted_payload})
    return {"ok": True, "output": res.get("output", "")}
