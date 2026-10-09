"""Ticket #8: real gateway tool functions vs recorded engine payloads."""

from __future__ import annotations

import json
from hashlib import sha256

import gateway.server as gw_server
import httpx
import pytest
import replay
from panel_api import ssrf_guard


@pytest.fixture(autouse=True)
def _dns_global(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "resolve_host", lambda host, timeout=3.0: ["93.184.216.34"])
    monkeypatch.delenv("SWARM_SSRF_RESOLVER_FAIL_CLOSED", raising=False)


@pytest.mark.asyncio
async def test_web_search_parses_recorded_payload(monkeypatch):
    replay.install(monkeypatch, replay.searxng_transport())
    res = await gw_server.web_search(query="recorded query", limit=10)
    assert res["is_error"] is False
    structured = gw_server.WebSearchStructured.model_validate(res["structured"])
    assert [it.rank for it in structured.results] == [1, 2]
    assert structured.results[0].title == "Recorded One"
    assert structured.results[0].url == "https://example.com/recorded-1"
    assert structured.results[0].snippet == "Recorded snippet one."
    assert "Title: Recorded One" in res["text"]


@pytest.mark.asyncio
async def test_web_search_limit_slices_recorded_results(monkeypatch):
    replay.install(monkeypatch, replay.searxng_transport())
    res = await gw_server.web_search(query="q", limit=1)
    assert len(res["structured"]["results"]) == 1


@pytest.mark.asyncio
async def test_fetch_page_md_path_extracts_metadata(monkeypatch):
    replay.install(monkeypatch, replay.crawl4ai_transport(md=replay.load("crawl4ai_md.json")))
    res = await gw_server.fetch_page(url="https://example.com/recorded")
    assert res["is_error"] is False
    structured = gw_server.FetchPageStructured.model_validate(res["structured"])
    assert structured.format == "markdown"
    assert structured.title == "Recorded Page"
    assert structured.final_url == "https://example.com/recorded"
    assert structured.status_code == 200
    assert structured.content_type == "text/markdown"
    assert structured.content_sha256 == sha256(structured.content.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_fetch_page_crawl_fallback_dict_case(monkeypatch):
    """The /crawl fallback dict case flagged in the #8 map: results[0] is a
    dict that may carry markdown, url and status alongside extras."""
    replay.install(monkeypatch, replay.crawl4ai_transport(md=None))
    res = await gw_server.fetch_page(url="https://example.com/needs-crawl")
    assert res["is_error"] is False
    structured = gw_server.FetchPageStructured.model_validate(res["structured"])
    assert structured.format == "markdown"
    assert structured.final_url == "https://example.com/fallback-final"
    assert "# Fallback" in structured.content


@pytest.mark.asyncio
async def test_tool_error_paths_map_to_codes(monkeypatch):
    def _boom(request):
        raise ConnectionError("engine down")

    replay.install(monkeypatch, httpx.MockTransport(_boom))
    res = await gw_server.fetch_page(url="https://example.com/")
    assert res["is_error"] is True and res["structured"]["code"] == "upstream_error"

    def _slow(request):
        raise httpx.TimeoutException("too slow", request=request)

    replay.install(monkeypatch, httpx.MockTransport(_slow))
    res = await gw_server.fetch_page(url="https://example.com/")
    assert res["structured"]["code"] == "upstream_timeout"
    res = await gw_server.web_search(query="q")
    assert res["structured"]["code"] == "upstream_timeout"


@pytest.mark.asyncio
async def test_tool_errors_scrub_internal_urls(monkeypatch):
    def _leak(request):
        raise ConnectionError(f"dial {gw_server.CRAWL4AI_URL} refused")

    replay.install(monkeypatch, httpx.MockTransport(_leak))
    res = await gw_server.fetch_page(url="https://example.com/")
    assert gw_server.CRAWL4AI_URL not in res["text"]
    assert gw_server.CRAWL4AI_URL not in json.dumps(res["structured"])


@pytest.mark.asyncio
async def test_fetch_page_ssrf_denial_needs_no_engine(monkeypatch):
    def _no_egress(request):
        raise AssertionError("engine must not be contacted")

    replay.install(monkeypatch, httpx.MockTransport(_no_egress))
    res = await gw_server.fetch_page(url="http://169.254.169.254/")
    assert res["is_error"] is True
    assert res["structured"]["code"] == "ssrf_denied"


def test_manifest_declares_mode_and_payload_shapes():
    manifest = replay.load("MANIFEST.json")
    assert manifest["mode"] in ("synthetic-v1", "recorded-v1")
    searxng = replay.load("searxng_basic.json")
    assert isinstance(searxng["results"], list) and searxng["results"]
    assert {"title", "url"} <= set(searxng["results"][0])
    crawl = replay.load("crawl4ai_crawl.json")
    assert isinstance(crawl["results"], list) and crawl["results"]
