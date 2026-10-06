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


def _row_health(container: dict[str, Any]) -> str:
    return str(container.get("Health") or "").lower()


def _live_service_names(containers: list[dict[str, Any]]) -> set[str]:
    """Service ids with a healthy ``running`` container row.

    Retry1 QA-B #2/#5: ``running`` alone is not enough -- rows with
    ``Health`` containing ``unhealthy`` or ``starting`` are pending repair
    and excluded so panel-api drift aligns with swarmd ``detect_drift``.
    Rows without a healthcheck (``Health == ""``, e.g. distroless
    egress-web) count as healthy when ``State == running``.

    ``docker compose ps --format json`` rows carry ``Service`` + ``State``;
    the ``Name`` fallback strips the ``scraper-swarm-`` project prefix for
    older/foreign rows.
    """
    running: set[str] = set()
    for c in containers:
        state = str(c.get("State") or "").lower()
        if state != "running":
            continue
        health = _row_health(c)
        if "unhealthy" in health or "starting" in health:
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


async def _fetch_containers_for_drift(swarmd: SwarmdClient) -> list[dict[str, Any]]:
    """Fetch ``ps -a`` rows for drift (wires the ``get_ps_all`` intent).

    Retry1 QA-B #7: ``get_ps_all`` was a dead intent (status used only
    ``get_ps``, hiding exited rows). Prefer ``get_ps_all`` so exited vs
    missing vs unhealthy/starting reasons survive; fall back to ``get_ps``
    for older swarmd builds.
    """
    try:
        res = await swarmd.send_intent("get_ps_all", {})
        containers = res.get("containers", [])
        if isinstance(containers, list):
            return containers
    except Exception as e:
        logger.debug("get_ps_all unavailable, falling back to get_ps: %s", e)
    try:
        res = await swarmd.send_intent("get_ps", {})
        containers = res.get("containers", [])
        return containers if isinstance(containers, list) else []
    except Exception as e:
        logger.debug("Failed to query get_ps from swarmd: %s", e)
        return []


def compute_drift(installed_running: list[str], containers: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure drift join: DB ``running`` rows vs live containers.

    Retry1 QA-B #2/#5: aligned with swarmd ``detect_drift`` -- a DB row is
    drift unless its container is ``State == running`` with ``Health`` not
    in (``unhealthy``, ``starting``). Empty-health (no healthcheck) counts
    as healthy when running.

    Returns ``{"running": [...], "drift": [...], "degraded": [...]}`` where
    each degraded entry is ``{"service_id": sid, "reason": ...}`` with
    reason ``missing`` / ``<state>`` / ``unhealthy`` / ``starting``.
    """
    by_service: dict[str, dict[str, Any]] = {}
    for c in containers:
        svc = str(c.get("Service") or "")
        if not svc:
            name = str(c.get("Name") or c.get("Names") or "").strip().lstrip("/")
            if name.startswith("scraper-swarm-"):
                name = name.removeprefix("scraper-swarm-")
                if name.endswith("-1"):
                    name = name[: -len("-1")]
            svc = name
        if svc and svc not in by_service:
            by_service[svc] = c
    live = _live_service_names(containers)
    drift: list[str] = []
    degraded: list[dict[str, str]] = []
    for sid in sorted(set(installed_running)):
        row = by_service.get(sid)
        if row is None:
            reason = (
                "no live container for DB 'running' row "
                f"(expected scraper-swarm-{sid} running); "
                "auto-repair attempted or suspended -- see reconcile"
            )
            drift.append(sid)
            degraded.append({"service_id": sid, "reason": f"missing: {reason}"})
            continue
        state = str(row.get("State") or "").lower()
        health = _row_health(row)
        if state != "running":
            drift.append(sid)
            degraded.append(
                {
                    "service_id": sid,
                    "reason": (
                        f"{state or 'not-running'}: container State={state or '?'} "
                        f"(expected running); auto-repair attempted or suspended -- see reconcile"
                    ),
                }
            )
        elif "unhealthy" in health:
            drift.append(sid)
            degraded.append(
                {
                    "service_id": sid,
                    "reason": (
                        "unhealthy: running but failing healthcheck; "
                        "auto-repair attempted or suspended -- see reconcile"
                    ),
                }
            )
        elif "starting" in health:
            drift.append(sid)
            degraded.append(
                {
                    "service_id": sid,
                    "reason": (
                        "starting: running but healthcheck still starting (pending, not healthy); "
                        "awaiting healthy or repair -- see reconcile"
                    ),
                }
            )
    drift_sorted = sorted(drift)
    return {"running": sorted(live), "drift": drift_sorted, "degraded": degraded}


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
    ``ps -a`` so drift is explicit (``drift`` + ``degraded`` with reason,
    including ``unhealthy``/``starting`` health). On drift a background
    ``reconcile_now`` repair is triggered (at most one per cooldown
    window); the swarmd loop also self-heals on its interval. Note:
    ``SWARM_RECONCILE_INTERVAL_S=0`` disables only the periodic loop --
    status-triggered background repair and POST /services/reconcile still
    fire (see docs/operator-reconcile.md).
    """
    containers: list[dict[str, Any]] = await _fetch_containers_for_drift(swarmd)

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

    Phase 02-infra-reconcile retry1 QA-B #1: sends ``force=true`` so one
    repair attempt runs even when auto-repair is suspended; the report
    carries ``forced``/``resume_note`` and the audit row records the
    forced resume. May take up to ~3 minutes on image pulls.
    """
    try:
        res = await asyncio.wait_for(
            swarmd.send_intent("reconcile_now", {"force": True}), timeout=180.0
        )
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

    containers: list[dict[str, Any]] = await _fetch_containers_for_drift(swarmd)

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
            "forced": bool(report.get("forced", False)),
            "resume_note": report.get("resume_note", ""),
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
    """Fetches real container logs from swarmd.

    Retry1 QA-B #7: ``lines`` is bounded to 1..1000 here (and again in
    swarmd); out-of-range values are 400 without touching Docker, and raw
    docker stderr is never echoed (generic message only).
    """
    import re as _re

    if not _re.fullmatch(r"[a-z][a-z0-9-]{1,40}", service_id or ""):
        raise HTTPException(status_code=400, detail="Invalid service name")
    try:
        n = int(lines)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="lines must be 1..1000") from None
    if n < 1 or n > 1000:
        raise HTTPException(status_code=400, detail="lines must be 1..1000") from None
    try:
        res = await swarmd.send_intent("get_logs", {"service": service_id, "lines": n})
        return {"service": service_id, "logs": res.get("logs", "")}
    except HTTPException:
        raise
    except Exception:
        # Never echo docker stderr (oracle); swarmd already logs it.
        raise HTTPException(status_code=400, detail="Failed to fetch logs") from None


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
    swarmd: SwarmdClient = Depends(get_swarmd),
    user: SessionInfo = Depends(require_role("admin", "operator", "viewer")),
):
    """Runs automated smoke tests across all currently running engines.

    Retry1 QA-B #5: ``starting`` containers are reported explicitly (not
    counted healthy). When any desired container is ``starting``, the
    response includes ``starting: [...]`` with an explanatory note; each
    result already carries ``latency_ms`` (recorded, no pass/fail
    threshold -- see docs/operator-reconcile.md).
    """
    from panel_api.smoke_test import SmokeTestRunner

    query = "SELECT service_id FROM installed_services WHERE status != 'stopped'"
    async with db.conn.execute(query) as cur:
        rows = await cur.fetchall()
        services = [r["service_id"] for r in rows]

    # Explicit starting report: join live Health so a starting engine is
    # never silently counted as pass/fail without context.
    starting: list[str] = []
    try:
        ps = await swarmd.send_intent("get_ps_all", {})
        for c in ps.get("containers", []) or []:
            health = str(c.get("Health") or "").lower()
            if "starting" in health:
                svc = str(c.get("Service") or "")
                if svc and svc not in starting:
                    starting.append(svc)
    except Exception as e:
        logger.debug("test-all starting probe failed: %s", e)

    results = []
    for sid in services:
        results.append(await SmokeTestRunner.run(sid))
    # Always include egress security test
    results.append(await SmokeTestRunner.run("egress-guard"))

    resp: dict[str, Any] = {
        "total": len(results),
        "passed": sum(1 for r in results if r.get("passed")),
        "results": results,
        "starting": sorted(starting),
    }
    if starting:
        resp["starting_note"] = (
            f"{', '.join(sorted(starting))} container(s) Health=starting "
            "(pending, not healthy); results above ran anyway with latency_ms recorded"
        )
    return resp


@router.post("/{service_id}/restart")
async def restart_service(
    service_id: str,
    db: Database = Depends(get_db),
    swarmd: SwarmdClient = Depends(get_swarmd),
    user: SessionInfo = Depends(require_role("admin", "operator")),
):
    """Restarts one engine-stack service via the ``restart_service`` intent.

    Retry1 QA-B #7: wires the previously dead ``restart_service`` intent
    (authenticated admin/operator only) instead of leaving it as
    0660-socket dead surface. The service name is allowlisted before any
    Docker call; raw docker stderr is never echoed.
    """
    import re as _re

    if not _re.fullmatch(r"[a-z][a-z0-9-]{1,40}", service_id or ""):
        raise HTTPException(status_code=400, detail="Invalid service name")
    query = "SELECT profile FROM installed_services WHERE service_id = ?"
    async with db.conn.execute(query, (service_id,)) as cur:
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Service not installed")

    try:
        res = await swarmd.send_intent("restart_service", {"service": service_id})
    except Exception:
        raise HTTPException(status_code=400, detail="restart failed") from None

    audit_logger = AuditLogger(db.conn)
    await audit_logger.log(
        actor=user.username, action="restart_service", details={"service": service_id}
    )
    return {"ok": True, "output": res.get("output", "")}
