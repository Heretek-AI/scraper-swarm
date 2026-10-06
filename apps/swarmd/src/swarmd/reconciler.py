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
  re-deploy which resets the backoff, or POST /services/reconcile with
  ``force`` for one resume attempt) instead of hot-looping.
- Health ``starting`` is degraded/pending (never counted healthy or
  repaired until ``healthy``/running); services without a healthcheck
  (``Health == ""``, e.g. distroless egress-web) count as healthy when
  ``State == running``.
- Empty/unparseable compose and corrupt ``wanted.json`` are degraded with
  an alert-ready reason (never clean, never fail-open).
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
RECONCILE_HISTORY_FILE = "reconcile-history.jsonl"

# Consecutive failed repair passes before auto-repair suspends itself.
MAX_CONSECUTIVE_REPAIRS = 3

# Repair history depth (append-only jsonl, trimmed to this many rows).
HISTORY_LIMIT = 50

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
    """Returns the persisted desired ``wanted`` set, or None if never applied.

    Corrupt JSON (or a non-dict payload) also returns None here for
    backwards compatibility; use :func:`wanted_corrupt_reason` to
    distinguish "never deployed" from "corrupt -- needs operator action".
    """
    path = stack_dir / WANTED_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Ignoring unreadable %s: %s", path, e)
        return None


def wanted_corrupt_reason(stack_dir: Path) -> str | None:
    """Alert-ready reason when ``wanted.json`` exists but is unusable."""
    path = stack_dir / WANTED_FILE
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return f"wanted.json unreadable: {type(e).__name__}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return f"wanted.json corrupt JSON ({type(e).__name__}); re-deploy to restore desired state"
    if not isinstance(data, dict):
        return "wanted.json corrupt (expected object); re-deploy to restore desired state"
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
    # Phase 02-infra-reconcile retry1 QA-B #6: append-only history so a
    # single overwrite cannot lose the repair trail. Best-effort only.
    try:
        append_history(stack_dir, report)
    except Exception as e:  # noqa: BLE001 -- history must never fail the pass
        logger.warning("Could not append reconcile history: %s", e)


def append_history(stack_dir: Path, report: dict[str, Any]) -> None:
    """Appends one report row to the jsonl history, trimmed to HISTORY_LIMIT."""
    path = stack_dir / RECONCILE_HISTORY_FILE
    try:
        existing: list[str] = []
        if path.exists():
            raw = path.read_text(encoding="utf-8").splitlines()
            existing = [ln for ln in raw if ln.strip()]
        existing.append(json.dumps(report, sort_keys=True))
        trimmed = existing[-HISTORY_LIMIT:]
        path.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
        path.chmod(0o600)
    except OSError as e:
        logger.warning("Could not write %s: %s", path, e)


def load_history(stack_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Returns up to ``limit`` most-recent history rows (newest last)."""
    path = stack_dir / RECONCILE_HISTORY_FILE
    if not path.exists():
        return []
    try:
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                rows.append(data)
        return rows[-max(1, limit) :]
    except OSError:
        return []


def reset_repair_backoff(stack_dir: Path) -> None:
    """Resets the suspend counter after an explicit re-deploy.

    Called by ``apply_stack`` (which already rewrote ``wanted.json``) so a
    fixed daemon + fresh deploy resumes auto-repair instead of staying
    suspended forever (QA-B #1 deadlock). Preserves the degraded flag until
    the next pass verifies; never raises.
    """
    try:
        last = load_last_report(stack_dir)
        if not last:
            return
        if int(last.get("consecutive_repairs", 0) or 0) == 0:
            return
        last["consecutive_repairs"] = 0
        last["backoff_reset_at"] = _utcnow()
        last["backoff_reset_by"] = "re-deploy"
        save_last_report(stack_dir, last)
    except Exception as e:  # noqa: BLE001 -- reset is best-effort
        logger.warning("Could not reset repair backoff: %s", e)


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


def _health_lower(container: dict[str, Any]) -> str:
    return str(container.get("Health") or "").lower()


def is_healthy_row(container: dict[str, Any]) -> bool:
    """True only when the row counts as repaired/running.

    ``State`` must be ``running`` AND ``Health`` must not be
    ``unhealthy`` or ``starting``. Empty health (no healthcheck, e.g.
    distroless egress-web) counts as healthy when running.
    """
    if str(container.get("State") or "").lower() != "running":
        return False
    health = _health_lower(container)
    return "unhealthy" not in health and "starting" not in health


def detect_drift(
    desired: list[str], containers: list[dict[str, Any]]
) -> tuple[list[dict[str, str]], list[str]]:
    """Compares desired services vs ``ps -a`` rows.

    Returns (drift_entries, running_services). Each drift entry is
    ``{"service": sid, "reason": ...}`` where reason is one of
    ``missing`` (no container row at all), ``<state>`` (e.g. ``exited``),
    ``unhealthy`` (running but failing its healthcheck), or ``starting``
    (running but healthcheck still starting -- pending, never healthy).
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
        health = _health_lower(row)
        if state != "running":
            drift.append({"service": sid, "reason": state or "not-running"})
        elif "unhealthy" in health:
            drift.append({"service": sid, "reason": "unhealthy"})
        elif "starting" in health:
            drift.append({"service": sid, "reason": "starting"})
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
        desired, _reason = self.desired_services_detailed()
        return desired

    def desired_services_detailed(self) -> tuple[list[str], str | None]:
        """Desired services plus an alert-ready error when compose is unusable.

        Returns (desired, error). ``error`` is None on success; otherwise a
        human-readable reason (compose missing/empty/unparseable/no
        services) and ``desired`` is [].
        """
        content = self.orchestrator.get_compose_content()
        if not content or not content.strip():
            return [], "rendered compose missing or empty; re-deploy to restore desired state"
        import yaml  # local import: pyyaml is already a swarmd dependency

        try:
            doc = yaml.safe_load(content) or {}
        except yaml.YAMLError as e:
            logger.warning("Unparseable rendered compose: %s", e)
            return [], f"rendered compose unparseable ({type(e).__name__}); re-deploy to restore"
        if not isinstance(doc, dict):
            return [], "rendered compose unparseable (expected mapping); re-deploy to restore"
        services = doc.get("services") or {}
        if not isinstance(services, dict) or not services:
            return [], "rendered compose has no services; re-deploy to restore desired state"
        return sorted(str(s) for s in services), None

    async def run_pass(self, *, force: bool = False) -> dict[str, Any]:
        """One detect -> repair -> verify pass. Never raises (fail-degraded).

        ``force`` allows one repair attempt even when auto-repair is
        suspended (POST /services/reconcile resume path, QA-B #1). The
        forced attempt is marked ``forced: True`` with an audit note.
        """
        wanted_doc = load_wanted(self.stack_dir)
        if wanted_doc is None:
            corrupt = wanted_corrupt_reason(self.stack_dir)
            if corrupt is not None:
                previous = load_last_report(self.stack_dir) or {}
                consecutive = int(previous.get("consecutive_repairs", 0) or 0)
                report: dict[str, Any] = {
                    "checked_at": _utcnow(),
                    "enabled": True,
                    "interval_s": self.interval_s,
                    "desired": [],
                    "running": [],
                    "drift": [],
                    "action": "none",
                    "repaired": [],
                    "still_missing": [],
                    "consecutive_repairs": consecutive,
                    "degraded": True,
                    "reason": corrupt,
                }
                save_last_report(self.stack_dir, report)
                return report
            report = {
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

        desired, compose_error = self.desired_services_detailed()
        if compose_error is not None:
            # QA-B #3: empty/unparseable compose is degraded, never clean.
            # Preserve (do not reset) the suspend counter.
            report = {
                "checked_at": _utcnow(),
                "enabled": True,
                "interval_s": self.interval_s,
                "desired": [],
                "running": [],
                "drift": [],
                "action": "none",
                "repaired": [],
                "still_missing": [],
                "consecutive_repairs": consecutive,
                "degraded": True,
                "reason": compose_error,
            }
            save_last_report(self.stack_dir, report)
            return report
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

        if consecutive >= MAX_CONSECUTIVE_REPAIRS and not force:
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
                    "re-deploy (resets backoff) or POST /services/reconcile (forces one "
                    "resume attempt) to resume"
                ),
            }
            save_last_report(self.stack_dir, report)
            return report

        forced_note = (
            f"forced resume attempt after {consecutive} consecutive failed passes; "
            if force and consecutive >= MAX_CONSECUTIVE_REPAIRS
            else ""
        )

        # Repair: unhealthy-but-running services get an explicit restart
        # (plain `up -d` does not recycle them); missing/exited ones are
        # recreated by an idempotent `up -d --remove-orphans`. `starting`
        # rows are left to finish starting (no restart); they stay in
        # still_missing until healthy.
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
                "reason": f"{forced_note}repair raised {type(e).__name__}",
            }
            if force and consecutive >= MAX_CONSECUTIVE_REPAIRS:
                report["forced"] = True
            save_last_report(self.stack_dir, report)
            return report

        try:
            verify = await self.orchestrator.get_ps()
        except Exception as e:
            logger.warning("reconcile verify ps failed: %s", e)
            verify = []
        # QA-B #2: health-aware verify -- running-but-unhealthy/starting is
        # NOT repaired until healthy/running.
        healthy_now = {container_service(c) for c in verify if is_healthy_row(c)}
        repaired = sorted(d["service"] for d in drift if d["service"] in healthy_now)
        still_missing = sorted(d["service"] for d in drift if d["service"] not in healthy_now)
        new_consecutive = 0 if not still_missing else consecutive + 1
        report = {
            "checked_at": _utcnow(),
            "enabled": True,
            "interval_s": self.interval_s,
            "desired": desired,
            "running": sorted(healthy_now),
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
        if force and consecutive >= MAX_CONSECUTIVE_REPAIRS:
            report["forced"] = True
            report["resume_note"] = (
                "forced resume attempt after suspend (POST /services/reconcile); "
                "backoff counter updated from verify result"
            )
        if still_missing:
            report["reason"] = (
                f"{forced_note}repair incomplete after {new_consecutive} consecutive pass(es): "
                f"{', '.join(still_missing)} still not running"
            )
        save_last_report(self.stack_dir, report)
        return report
