"""Unit tests for the Phase 02-infra-reconcile status drift join.

Evidence: file:///home/john/Projects/scraper-swarm/.roadmap/01-live-smoke/dossier.json
("DB running vs 0 containers drift").
"""

from __future__ import annotations

from panel_api.routers.services import compute_drift


def _row(svc: str, state: str = "running") -> dict:
    return {"Service": svc, "Name": f"scraper-swarm-{svc}-1", "State": state}


def test_no_drift_when_containers_match_db():
    out = compute_drift(["searxng", "crawl4ai"], [_row("searxng"), _row("crawl4ai")])
    assert out["drift"] == []
    assert out["degraded"] == []
    assert sorted(out["running"]) == ["crawl4ai", "searxng"]


def test_01_escalation_drift_is_explicit():
    # 01-live-smoke escalated with DB running rows vs 0 containers.
    out = compute_drift(["searxng", "crawl4ai"], [])
    assert out["drift"] == ["crawl4ai", "searxng"]
    assert [d["service_id"] for d in out["degraded"]] == ["crawl4ai", "searxng"]
    assert all("reason" in d and d["reason"] for d in out["degraded"])


def test_exited_container_counts_as_drift():
    out = compute_drift(["searxng"], [_row("searxng", state="exited")])
    assert out["drift"] == ["searxng"]
    assert out["running"] == []


def test_stopped_db_rows_are_not_drift():
    # Only DB `running` rows are passed in; stopped rows never appear here.
    out = compute_drift([], [_row("searxng")])
    assert out["drift"] == []
    assert out["running"] == ["searxng"]


def test_name_fallback_parsing():
    out = compute_drift(["valkey"], [{"Name": "scraper-swarm-valkey-1", "State": "Running"}])
    assert out["drift"] == []
