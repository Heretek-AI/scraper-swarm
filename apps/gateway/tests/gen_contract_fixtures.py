"""Generates versioned golden contract fixtures (ticket #8).

Runs the REAL gateway app over ASGI (no network: engines via replay
transports, DNS stubbed) and writes
``contract/v1/fixtures/<area>/<case>.json``::

    {contract_version, request, response_status, response_headers, response_body}

Volatile fields are normalized (``fetched_at`` -> ``<fetched_at>``,
``Retry-After`` -> ``<retry_after_s>``) so fixtures are byte-stable and the
drift test (``test_contract_drift.py``) can compare exactly. Consumers vendor
these by commit — see ``contract/v1/README.md``.

Usage: ``python apps/gateway/tests/gen_contract_fixtures.py`` (repo root).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]
FIXTURE_ROOT = REPO_ROOT / "contract" / "v1" / "fixtures"

sys.path.insert(0, str(TESTS_DIR))

import httpx  # noqa: E402
import replay  # noqa: E402


def _normalize(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "fetched_at" and isinstance(v, str):
                out[k] = "<fetched_at>"
            else:
                out[k] = _normalize(v)
        return out
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj


def _fixture(request: Any, status: int, headers: dict, body: Any) -> dict:
    headers = {k.lower(): v for k, v in headers.items()}
    subset = {k: headers[k] for k in ("content-type", "x-swarm-contract") if k in headers}
    if "retry-after" in headers:
        subset["retry-after"] = "<retry_after_s>"
    return {
        "contract_version": 1,
        "request": request,
        "response_status": status,
        "response_headers": subset,
        "response_body": _normalize(body),
    }


async def build_fixtures() -> dict[str, dict]:
    """Builds {relative-path: fixture} without touching the disk."""
    import gateway.server as gw_server
    from httpx import ASGITransport, AsyncClient
    from panel_api import ssrf_guard
    from panel_api.db import Database

    fixtures: dict[str, dict] = {}
    tmp = Path(tempfile.mkdtemp(prefix="contract-fixtures-"))
    db = Database(tmp / "panel.db")
    await db.connect()

    async def seed(key_id: str, raw: str, scopes: list[str], rpm: int = 1000) -> None:
        await db.conn.execute(
            "INSERT INTO agent_keys (id, name, key_hash, key_prefix, scopes, rate_limit_rpm)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                key_id,
                f"agent-{key_id}",
                hashlib.sha256(raw.encode()).hexdigest(),
                raw[:14] + "...",
                json.dumps(scopes),
                rpm,
            ),
        )
        await db.conn.commit()

    full_raw = "swarm_sec_fixturefull001"
    search_raw = "swarm_sec_fixturesearch01"
    rl_raw = "swarm_sec_fixturerate0001"
    await seed("k-full", full_raw, ["search", "scrape"])
    await seed("k-search", search_raw, ["search"])
    await seed("k-rl", rl_raw, ["search"], rpm=1)

    gw_server.DB_PATH = str(tmp / "panel.db")
    gw_server._reset_rate_limits()
    real_resolve = ssrf_guard.resolve_host
    ssrf_guard.resolve_host = lambda host, timeout=3.0: ["93.184.216.34"]
    real_env = dict(os.environ)
    os.environ.pop("SWARM_DEPLOYED_ENGINES", None)

    def auth(raw: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {raw}"}

    async def post(client: AsyncClient, raw: str, body: dict) -> httpx.Response:
        return await client.post("/mcp", headers=auth(raw), json=body)

    try:
        transport = ASGITransport(app=gw_server.app)
        async with AsyncClient(transport=transport, base_url="http://gateway") as client:
            # initialize + tools/list
            r = await post(client, full_raw, {"jsonrpc": "2.0", "method": "initialize", "id": 1})
            fixtures["initialize/success.json"] = _fixture(
                {"jsonrpc": "2.0", "method": "initialize", "id": 1},
                r.status_code,
                dict(r.headers),
                r.json(),
            )
            r = await post(client, full_raw, {"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            fixtures["tools-list/full.json"] = _fixture(
                {"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                r.status_code,
                dict(r.headers),
                r.json(),
            )
            r = await post(client, search_raw, {"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            fixtures["tools-list/search-only.json"] = _fixture(
                {"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                r.status_code,
                dict(r.headers),
                r.json(),
            )

            def call(tool: str, args: dict, rpc_id: int = 1) -> dict:
                return {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": args},
                    "id": rpc_id,
                }

            # web_search success + error (replay, no network)
            with replay.routed(replay.searxng_transport()):
                req = call("web_search", {"query": "recorded query", "limit": 2})
                r = await post(client, full_raw, req)
                fixtures["web-search/success.json"] = _fixture(
                    req, r.status_code, dict(r.headers), r.json()
                )

            def _down(request: httpx.Request) -> httpx.Response:
                raise ConnectionError("engine down")

            with replay.routed(httpx.MockTransport(_down)):
                req = call("web_search", {"query": "q"})
                r = await post(client, full_raw, req)
                fixtures["web-search/upstream-error.json"] = _fixture(
                    req, r.status_code, dict(r.headers), r.json()
                )

            # fetch_page: md success, crawl fallback, denials, engine errors
            with replay.routed(replay.crawl4ai_transport(md=replay.load("crawl4ai_md.json"))):
                req = call("fetch_page", {"url": "https://example.com/recorded"})
                r = await post(client, full_raw, req)
                fixtures["fetch-page/md-success.json"] = _fixture(
                    req, r.status_code, dict(r.headers), r.json()
                )
            with replay.routed(replay.crawl4ai_transport(md=None)):
                req = call("fetch_page", {"url": "https://example.com/needs-crawl"})
                r = await post(client, full_raw, req)
                fixtures["fetch-page/crawl-fallback.json"] = _fixture(
                    req, r.status_code, dict(r.headers), r.json()
                )

            req = call("fetch_page", {"url": "http://169.254.169.254/"})
            r = await post(client, full_raw, req)
            fixtures["fetch-page/ssrf-denied.json"] = _fixture(
                req, r.status_code, dict(r.headers), r.json()
            )

            with replay.routed(httpx.MockTransport(_down)):
                req = call("fetch_page", {"url": "https://example.com/"})
                r = await post(client, full_raw, req)
                fixtures["fetch-page/upstream-error.json"] = _fixture(
                    req, r.status_code, dict(r.headers), r.json()
                )

            def _slow(request: httpx.Request) -> httpx.Response:
                raise httpx.TimeoutException("too slow", request=request)

            with replay.routed(httpx.MockTransport(_slow)):
                req = call("fetch_page", {"url": "https://example.com/"})
                r = await post(client, full_raw, req)
                fixtures["fetch-page/upstream-timeout.json"] = _fixture(
                    req, r.status_code, dict(r.headers), r.json()
                )

            os.environ["SWARM_DEPLOYED_ENGINES"] = "searxng"
            req = call("fetch_page", {"url": "https://example.com/"})
            r = await post(client, full_raw, req)
            fixtures["fetch-page/engine-unavailable.json"] = _fixture(
                req, r.status_code, dict(r.headers), r.json()
            )
            os.environ.pop("SWARM_DEPLOYED_ENGINES", None)

            # deep_research + stealth_scrape successes (recorded engine text)
            with replay.routed(
                replay.replay_transport([("POST", "/research", (200, "Recorded research report."))])
            ):
                req = call("deep_research", {"query": "recorded topic"})
                r = await post(client, full_raw, req)
                fixtures["deep-research/success.json"] = _fixture(
                    req, r.status_code, dict(r.headers), r.json()
                )
            with replay.routed(
                replay.replay_transport([("POST", "/fetch", (200, "Recorded stealth content."))])
            ):
                req = call("stealth_scrape", {"url": "https://example.com/"})
                r = await post(client, full_raw, req)
                fixtures["stealth-scrape/success.json"] = _fixture(
                    req, r.status_code, dict(r.headers), r.json()
                )
            with replay.routed(httpx.MockTransport(_down)):
                req = call("deep_research", {"query": "q"})
                r = await post(client, full_raw, req)
                fixtures["deep-research/upstream-error.json"] = _fixture(
                    req, r.status_code, dict(r.headers), r.json()
                )

            req = call("stealth_scrape", {"url": "http://10.9.9.9/"})
            r = await post(client, full_raw, req)
            fixtures["stealth-scrape/ssrf-denied.json"] = _fixture(
                req, r.status_code, dict(r.headers), r.json()
            )

            # error surface: invalid params, unknown tool, scope denied, rate limit
            req = call("web_search", {"limit": 3})
            r = await post(client, full_raw, req)
            fixtures["tools-call/invalid-params.json"] = _fixture(
                req, r.status_code, dict(r.headers), r.json()
            )
            req = call("nope_tool", {})
            r = await post(client, full_raw, req)
            fixtures["tools-call/unknown-tool.json"] = _fixture(
                req, r.status_code, dict(r.headers), r.json()
            )

            req = call("fetch_page", {"url": "https://example.com/"})
            r = await post(client, search_raw, req)
            fixtures["tools-call/scope-denied.json"] = _fixture(
                req, r.status_code, dict(r.headers), r.json()
            )

            rl_body = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
            await post(client, rl_raw, rl_body)
            r = await post(client, rl_raw, rl_body)
            assert r.status_code == 429
            fixtures["rate-limited/rate-limited.json"] = _fixture(
                rl_body, r.status_code, dict(r.headers), r.json()
            )
    finally:
        ssrf_guard.resolve_host = real_resolve
        os.environ.clear()
        os.environ.update(real_env)
        gw_server._reset_rate_limits()
        await db.close()

    return fixtures


def main() -> int:
    fixtures = asyncio.run(build_fixtures())
    for rel, fixture in sorted(fixtures.items()):
        path = FIXTURE_ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {len(fixtures)} fixtures to {FIXTURE_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
