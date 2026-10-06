"""Desired-state reconciler: repairs DB-vs-Docker drift for the engine stack.

Phase 02-infra-reconcile (persistence/reconciliation only):
- 01-live-smoke escalated with ``installed_services=[searxng running,
  crawl4ai running]`` vs ``docker ps 0x scraper-swarm-*`` drift and
  ``test-all 1/3`` (evidence: file:///home/john/Projects/scraper-swarm/.roadmap/01-live-smoke/dossier.json).
- This module persists the last applied ``wanted`` set (``wanted.json`` in
  the stack dir, which lives on the ``swarm_data`` volume so it survives
  swarmd restarts/rebuilds) and, on a timer plus on demand, compares the
  desired services from the rendered compose against live
  ``docker compose ps -a`` state. Missing / exited containers are brought
  back with an idempotent ``up -d``; containers reported ``unhealthy`` by
  their healthcheck are ``restart``ed (health-driven restart).
- After ``MAX_CONSECUTIVE_REPAIRS`` failed passes the reconciler marks the
  stack ``degraded`` with a reason and suspends auto-repair (operator must
  re-deploy or POST /services/reconcile) instead of hot-looping.
- Reports carry service names, container states, and latencies only --
  never secrets or env values -- so they are safe for audit rows and API
  responses (evidence: file:///home/john/Projects/scraper-swarm/apps/panel-api/src/panel_api/smoke_test.py).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("swarmd.reconcile")

WANTED_FILE = "wanted.json"
RECONCILE_STATE_FILE = "reconcile.json"

# Consecutive failed repair passes before auto-repair suspends itself.
MAX_CONSECUTIVE_REPAIRS = 3

_SERVICE_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


class _Orchestrator(Protocol):
    async def ps_all(self) -> list[dict[str, Any]]: ...
    async def get_ps(self) -> list[dict[str, Any]]: ...
    async def ensure_up(self) -> str: ...
    async def restart(self, service: str) -> str: ...
    def get_compose_content(self) -> str: ...


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def load_wanted(stack_dir: Path) -> dict[str, Any] | None:
    """Returns the persisted desired ``wanted`` set, or None if never applied."""
    path = stack_dir / WANTED_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Ignoring unreadable %s: %s", path, e)
        return None


def save_wanted(stack_dir: Path, wanted: dict[str, Any]) -> None:
    path = stack_dir / WANTED_FILE
    path.write_text(
        json.dumps(
            {"wanted": wanted, "updated_at": _utcnow()},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def clear_wanted(stack_dir: Path) -> None:
    for name in (WANTED_FILE, RECONCILE_STATE_FILE):
        try:
            (stack_dir / name).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Could not remove %s: %s", name, e)


def load_last_report(stack_dir: Path) -> dict[str, Any] | None:
    path = stack_dir / RECONCILE_STATE_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_last_report(stack_dir: Path, report: dict[str, Any]) -> None:
    path = stack_dir / RECONCILE_STATE_FILE
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def container_service(container: dict[str, Any]) -> str:
    """Best-effort service name for one ``docker compose ps`` JSON row."""
    svc = str(container.get("Service") or "")
    if svc:
        return svc
    name = str(container.get("Name") or container.get("Names") or "")
    # Container names look like ``scraper-swarm-<service>-1`` or
    # ``scraper-swarm-<service>``; strip the project prefix/suffix.
    name = name.strip().lstrip("/")
    if name.startswith("scraper-swarm-"):
        name = name.removeprefix("scraper-swarm-")
        if name.endswith("-1") and len(name) > 2:
            name = name[: -len("-1")]
    return name


def detect_drift(
    desired: list[str], containers: list[dict[str, Any]]
) -> tuple[list[dict[str, str]], list[str]]:
    """Compares desired services vs ``ps -a`` rows.

    Returns (drift_entries, running_services). Each drift entry is
    ``{"service": sid, "reason": ...}`` where reason is one of
    ``missing`` (no container row at all), ``<state>`` (e.g. ``exited``),
    or ``unhealthy`` (running but failing its healthcheck).
    """
    by_service: dict[str, dict[str, Any]] = {}
    for c in containers:
        by_service.setdefault(container_service(c), c)
    running: list[str] = []
    drift: list[dict[str, str]] = []
    for sid in sorted(desired):
        row = by_service.get(sid)
        if row is None:
            drift.append({"service": sid, "reason": "missing"})
            continue
        state = str(row.get("State") or "").lower()
        health = str(row.get("Health") or "").lower()
        if state != "running":
            drift.append({"service": sid, "reason": state or "not-running"})
        elif "unhealthy" in health:
            drift.append({"service": sid, "reason": "unhealthy"})
        else:
            running.append(sid)
    return drift, running


def _valid_service(name: str) -> bool:
    return bool(_SERVICE_RE.fullmatch(name))


class Reconciler:
    """Periodic + on-demand drift repair for one rendered engine stack."""

    def __init__(self, stack_dir: Path, orchestrator: _Orchestrator, interval_s: int = 60):
        self.stack_dir = stack_dir
        self.orchestrator = orchestrator
        self.interval_s = max(0, int(interval_s))

    @property
    def enabled(self) -> bool:
        return self.interval_s > 0 and load_wanted(self.stack_dir) is not None

    def desired_services(self) -> list[str]:
        """Desired service ids from the rendered compose on disk."""
        content = self.orchestrator.get_compose_content()
        if not content:
            return []
        import yaml  # local import: pyyaml is already a swarmd dependency

        try:
            doc = yaml.safe_load(content) or {}
        except yaml.YAMLError as e:
            logger.warning("Unparseable rendered compose: %s", e)
            return []
        services = doc.get("services") or {}
        return sorted(str(s) for s in services)

    async def run_pass(self) -> dict[str, Any]:
        """One detect -> repair -> verify pass. Never raises (fail-degraded)."""
        wanted_doc = load_wanted(self.stack_dir)
        if wanted_doc is None:
            report: dict[str, Any] = {
                "checked_at": _utcnow(),
                "enabled": False,
                "reason": "no desired state: apply a stack before reconcile can run",
                "desired": [],
                "running": [],
                "drift": [],
                "action": "none",
                "repaired": [],
                "still_missing": [],
                "consecutive_repairs": 0,
                "degraded": False,
            }
            save_last_report(self.stack_dir, report)
            return report

        previous = load_last_report(self.stack_dir) or {}
        consecutive = int(previous.get("consecutive_repairs", 0) or 0)

        desired = self.desired_services()
        try:
            containers = await self.orchestrator.ps_all()
        except Exception as e:
            logger.warning("reconcile ps failed: %s", e)
            report = {
                "checked_at": _utcnow(),
                "enabled": True,
                "interval_s": self.interval_s,
                "desired": desired,
                "running": [],
                "drift": [],
                "action": "none",
                "repaired": [],
                "still_missing": [],
                "consecutive_repairs": consecutive,
                "degraded": True,
                "reason": f"ps unreachable: {type(e).__name__}",
            }
            save_last_report(self.stack_dir, report)
            return report

        drift, running = detect_drift(desired, containers)
        if not drift:
            report = {
                "checked_at": _utcnow(),
                "enabled": True,
                "interval_s": self.interval_s,
                "desired": desired,
                "running": running,
                "drift": [],
                "action": "none",
                "repaired": [],
                "still_missing": [],
                "consecutive_repairs": 0,
                "degraded": False,
            }
            save_last_report(self.stack_dir, report)
            return report

        if consecutive >= MAX_CONSECUTIVE_REPAIRS:
            report = {
                "checked_at": _utcnow(),
                "enabled": True,
                "interval_s": self.interval_s,
                "desired": desired,
                "running": running,
                "drift": drift,
                "action": "suspended",
                "repaired": [],
                "still_missing": [d["service"] for d in drift],
                "consecutive_repairs": consecutive,
                "degraded": True,
                "reason": (
                    f"auto-repair suspended after {consecutive} consecutive failed passes; "
                    "re-deploy or POST /services/reconcile to resume"
                ),
            }
            save_last_report(self.stack_dir, report)
            return report

        # Repair: unhealthy-but-running services get an explicit restart
        # (plain `up -d` does not recycle them); missing/exited ones are
        # recreated by an idempotent `up -d --remove-orphans`.
        action_notes: list[str] = []
        try:
            for entry in drift:
                if entry["reason"] == "unhealthy" and _valid_service(entry["service"]):
                    await self.orchestrator.restart(entry["service"])
                    action_notes.append(f"restart {entry['service']}")
            output = await self.orchestrator.ensure_up()
            action_notes.append("up -d")
        except Exception as e:
            logger.warning("reconcile repair failed: %s", e)
            report = {
                "checked_at": _utcnow(),
                "enabled": True,
                "interval_s": self.interval_s,
                "desired": desired,
                "running": running,
                "drift": drift,
                "action": "repair-failed",
                "repaired": [],
                "still_missing": [d["service"] for d in drift],
                "consecutive_repairs": consecutive + 1,
                "degraded": True,
                "reason": f"repair raised {type(e).__name__}",
            }
            save_last_report(self.stack_dir, report)
            return report

        try:
            verify = await self.orchestrator.get_ps()
        except Exception as e:
            logger.warning("reconcile verify ps failed: %s", e)
            verify = []
        running_now = {
            container_service(c) for c in verify if str(c.get("State") or "").lower() == "running"
        }
        repaired = sorted(d["service"] for d in drift if d["service"] in running_now)
        still_missing = sorted(d["service"] for d in drift if d["service"] not in running_now)
        new_consecutive = 0 if not still_missing else consecutive + 1
        report = {
            "checked_at": _utcnow(),
            "enabled": True,
            "interval_s": self.interval_s,
            "desired": desired,
            "running": sorted(running_now),
            "drift": drift,
            "action": "+".join(action_notes) if action_notes else "none",
            "repaired": repaired,
            "still_missing": still_missing,
            "consecutive_repairs": new_consecutive,
            "degraded": bool(still_missing),
            # `up -d` output is container action lines (names/images only);
            # truncated and never includes env/secret values.
            "output_tail": str(output)[-2000:],
        }
        if still_missing:
            report["reason"] = (
                f"repair incomplete after {new_consecutive} consecutive pass(es): "
                f"{', '.join(still_missing)} still not running"
            )
        save_last_report(self.stack_dir, report)
        return report
