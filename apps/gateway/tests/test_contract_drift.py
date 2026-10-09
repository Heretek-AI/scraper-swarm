"""Ticket #8: golden contract fixtures must match current behaviour exactly."""

from __future__ import annotations

import json
from pathlib import Path

import gen_contract_fixtures
import pytest

REQUIRED = {
    "initialize/success.json",
    "tools-list/full.json",
    "tools-list/search-only.json",
    "web-search/success.json",
    "web-search/upstream-error.json",
    "fetch-page/md-success.json",
    "fetch-page/crawl-fallback.json",
    "fetch-page/ssrf-denied.json",
    "fetch-page/upstream-error.json",
    "fetch-page/upstream-timeout.json",
    "fetch-page/engine-unavailable.json",
    "deep-research/success.json",
    "deep-research/upstream-error.json",
    "stealth-scrape/success.json",
    "stealth-scrape/ssrf-denied.json",
    "tools-call/invalid-params.json",
    "tools-call/unknown-tool.json",
    "tools-call/scope-denied.json",
    "rate-limited/rate-limited.json",
}


def _fixture_root() -> Path:
    return (
        Path(gen_contract_fixtures.__file__).resolve().parents[3] / "contract" / "v1" / "fixtures"
    )


@pytest.mark.asyncio
async def test_published_fixtures_match_current_behaviour():
    """Any /mcp behaviour change without regenerated fixtures fails here."""
    expected = await gen_contract_fixtures.build_fixtures()
    missing = REQUIRED - set(expected)
    assert not missing, missing
    root = _fixture_root()
    on_disk = {str(p.relative_to(root)) for p in root.rglob("*.json")}
    assert not (REQUIRED - on_disk), REQUIRED - on_disk
    for rel, fresh in sorted(expected.items()):
        on_disk = json.loads((root / rel).read_text())
        assert on_disk == fresh, rel


def test_fixture_envelopes_are_well_formed():
    root = _fixture_root()
    for path in sorted(root.rglob("*.json")):
        fixture = json.loads(path.read_text())
        assert fixture["contract_version"] == 1, path
        assert isinstance(fixture["request"], dict), path
        assert isinstance(fixture["response_status"], int), path
        assert fixture["response_headers"].get("x-swarm-contract") == "1", path
        assert isinstance(fixture["response_body"], dict), path
