"""Unit tests for the Phase 02-infra-reconcile drift reconciler.

No Docker daemon is needed: a fake orchestrator stands in for
``DockerOrchestrator``. Evidence:
file:///home/john/Projects/scraper-swarm/.roadmap/01-live-smoke/dossier.json
("DB running vs 0 containers drift").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmd.docker_engine import DockerExecutionError, DockerOrchestrator
from swarmd.reconciler import (
    Reconciler,
    clear_wanted,
    container_service,
    detect_drift,
    load_last_report,
    load_wanted,
    save_wanted,
)


class FakeOrchestrator:
    def __init__(self, ps_rows: list[dict[str, Any]], compose: str = ""):
        self.ps_rows = ps_rows
        self.compose = compose
        self.ensure_up_calls = 0
        self.restarts: list[str] = []
        self.fail_up = False

    async def ps_all(self) -> list[dict[str, Any]]:
        return self.ps_rows

    async def get_ps(self) -> list[dict[str, Any]]:
        return [r for r in self.ps_rows if str(r.get("State", "")).lower() == "running"]

    async def ensure_up(self) -> str:
        self.ensure_up_calls += 1
        if self.fail_up:
            raise RuntimeError("daemon down")
        # Model `up -d --remove-orphans`: existing rows recover, and rows
        # for every desired compose service exist afterwards.
        import yaml

        try:
            desired = list((yaml.safe_load(self.compose) or {}).get("services", {}))
        except yaml.YAMLError:
            desired = []
        have = {container_service(r) for r in self.ps_rows}
        for svc in desired:
            if svc not in have:
                self.ps_rows.append(
                    {"Service": svc, "Name": f"scraper-swarm-{svc}-1", "State": "running"}
                )
        for r in self.ps_rows:
            r["State"] = "running"
            r.pop("Health", None)
        return "up -d ok"

    async def restart(self, service: str) -> str:
        self.restarts.append(service)
        for r in self.ps_rows:
            if container_service(r) == service:
                r["State"] = "running"
                r.pop("Health", None)
        return f"restarted {service}"

    def get_compose_content(self) -> str:
        return self.compose


COMPOSE = """\
name: scraper-swarm
services:
  searxng: {}
  crawl4ai: {}
  valkey: {}
  egress-web: {}
"""


def _running(svc: str, health: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {
        "Service": svc,
        "Name": f"scraper-swarm-{svc}-1",
        "State": "running",
    }
    if health:
        row["Health"] = health
    return row


def _exited(svc: str) -> dict[str, Any]:
    return {"Service": svc, "Name": f"scraper-swarm-{svc}-1", "State": "exited"}


_NO_WANTED: Any = object()  # sentinel: do not write wanted.json at all
_DEFAULT_WANTED: dict[str, Any] = {"seed": True}


def _rec(
    tmp_path: Path, orch: FakeOrchestrator, wanted: dict[str, Any] | Any = _DEFAULT_WANTED
) -> Reconciler:
    if wanted is not _NO_WANTED:
        save_wanted(tmp_path, wanted)
    return Reconciler(stack_dir=tmp_path, orchestrator=orch, interval_s=60)


@pytest.mark.asyncio
async def test_disabled_without_desired_state(tmp_path: Path):
    orch = FakeOrchestrator([], COMPOSE)
    rec = _rec(tmp_path, orch, wanted=_NO_WANTED)
    assert not rec.enabled
    report = await rec.run_pass()
    assert report["enabled"] is False
    assert report["action"] == "none"


@pytest.mark.asyncio
async def test_no_drift_resets_counter(tmp_path: Path):
    orch = FakeOrchestrator(
        [_running("searxng"), _running("crawl4ai"), _running("valkey"), _running("egress-web")],
        COMPOSE,
    )
    rec = _rec(tmp_path, orch)
    report = await rec.run_pass()
    assert report["drift"] == []
    assert report["action"] == "none"
    assert report["degraded"] is False
    assert report["consecutive_repairs"] == 0
    assert orch.ensure_up_calls == 0


@pytest.mark.asyncio
async def test_missing_containers_are_repaired(tmp_path: Path):
    orch = FakeOrchestrator([], COMPOSE)  # the 01 drift: 0 containers
    rec = _rec(tmp_path, orch)
    report = await rec.run_pass()
    assert orch.ensure_up_calls == 1
    assert sorted(report["repaired"]) == ["crawl4ai", "egress-web", "searxng", "valkey"]
    assert report["still_missing"] == []
    assert report["degraded"] is False


@pytest.mark.asyncio
async def test_exited_container_repaired_with_reason(tmp_path: Path):
    orch = FakeOrchestrator(
        [_running("searxng"), _exited("crawl4ai"), _running("valkey"), _running("egress-web")],
        COMPOSE,
    )
    rec = _rec(tmp_path, orch)
    report = await rec.run_pass()
    assert {"service": "crawl4ai", "reason": "exited"} in report["drift"]
    assert report["repaired"] == ["crawl4ai"]
    assert report["degraded"] is False


@pytest.mark.asyncio
async def test_unhealthy_container_is_restarted(tmp_path: Path):
    orch = FakeOrchestrator(
        [_running("searxng", health="unhealthy"), _running("valkey")],
        "name: scraper-swarm\nservices:\n  searxng: {}\n  valkey: {}\n",
    )
    rec = _rec(tmp_path, orch)
    report = await rec.run_pass()
    assert orch.restarts == ["searxng"]
    assert report["repaired"] == ["searxng"]
    assert report["degraded"] is False


@pytest.mark.asyncio
async def test_repair_backoff_suspends_after_max_failures(tmp_path: Path):
    orch = FakeOrchestrator([], COMPOSE)
    orch.fail_up = True
    rec = _rec(tmp_path, orch)
    last = None
    for _ in range(4):
        last = await rec.run_pass()
    assert last is not None
    assert last["consecutive_repairs"] >= 3
    assert last["degraded"] is True
    calls_before = orch.ensure_up_calls
    suspended = await rec.run_pass()
    assert suspended["action"] == "suspended"
    assert orch.ensure_up_calls == calls_before  # no more docker calls
    assert "suspended" in suspended["reason"]


@pytest.mark.asyncio
async def test_ps_failure_degrades_without_raising(tmp_path: Path):
    class Broken(FakeOrchestrator):
        async def ps_all(self):
            raise RuntimeError("socket gone")

    rec = _rec(tmp_path, Broken([], COMPOSE))
    report = await rec.run_pass()
    assert report["degraded"] is True
    assert "ps unreachable" in report["reason"]


def test_detect_drift_reasons():
    drift, running = detect_drift(
        ["b", "a"], [_running("a"), _exited("b"), {"Service": "c", "State": "running"}]
    )
    assert drift == [{"service": "b", "reason": "exited"}]
    assert running == ["a"]


def test_container_service_name_fallbacks():
    assert container_service({"Service": "valkey"}) == "valkey"
    assert container_service({"Name": "scraper-swarm-searxng-1"}) == "searxng"
    assert container_service({"Name": "/scraper-swarm-crawl4ai"}) == "crawl4ai"


def test_wanted_roundtrip_and_clear(tmp_path: Path):
    assert load_wanted(tmp_path) is None
    save_wanted(tmp_path, {"searxng": {"profile": "standard", "params": {}}})
    assert (tmp_path / "wanted.json").stat().st_mode & 0o777 == 0o600
    assert load_wanted(tmp_path)["wanted"]["searxng"]["profile"] == "standard"
    _ = load_last_report(tmp_path)  # None before any pass; must not raise
    clear_wanted(tmp_path)
    assert load_wanted(tmp_path) is None


@pytest.mark.asyncio
async def test_restart_rejects_invalid_service_name(tmp_path: Path):
    orch = DockerOrchestrator(stack_dir=tmp_path)
    with pytest.raises(DockerExecutionError):
        await orch.restart("foo; rm -rf /")
    with pytest.raises(DockerExecutionError):
        await orch.restart("")
