"""Engine record/replay for gateway tool tests (ticket #8).

Behavioural basis: ext-orcareplay MatchingProxy (VCR-style record/replay —
clean-room reimplementation: no code copied). The replay side serves recorded
engine payloads through ``httpx.MockTransport`` so the REAL gateway tool
functions (URL building, parsing, /crawl fallback, error mapping) are tested
without network. ``record.py --record`` refreshes the payloads against a live
dev stack; the manifest records which mode the payloads are in.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parent / "engine_fixtures"

# Captured before any test patches it (install() rebinds this attribute on
# the shared httpx module object).
_REAL_CLIENT = httpx.AsyncClient


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def replay_transport(
    routes: list[tuple[str, str, Any]],
) -> httpx.MockTransport:
    """Builds a MockTransport from (method, path-suffix, outcome) routes.

    Outcome is a recorded JSON payload (served 200), a (status, payload)
    tuple, or an exception instance to raise (connection/timeout mapping).
    Unmatched calls raise — tests must declare every engine contact.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        for method, suffix, outcome in routes:
            if request.method == method and request.url.path.endswith(suffix):
                if isinstance(outcome, Exception):
                    raise outcome
                if isinstance(outcome, tuple):
                    status, payload = outcome
                else:
                    status, payload = 200, outcome
                if isinstance(payload, (dict, list)):
                    return httpx.Response(status, json=payload, request=request)
                return httpx.Response(status, text=str(payload), request=request)
        raise AssertionError(f"unrecorded engine call {request.method} {request.url}")

    return httpx.MockTransport(handler)


def searxng_transport(payload: Any | None = None, status: int = 200) -> httpx.MockTransport:
    return replay_transport(
        [
            (
                "GET",
                "/search",
                (status, payload if payload is not None else load("searxng_basic.json")),
            )
        ]
    )


def crawl4ai_transport(
    md: Any | None = None,
    crawl: Any | None = None,
    md_status: int = 200,
    crawl_status: int = 200,
) -> httpx.MockTransport:
    """Serves /md then /crawl. Pass md=None to force the /crawl fallback
    (non-200 /md), matching the gateway's real fallback behaviour."""
    routes: list[tuple[str, str, Any]] = []
    if md is None:
        routes.append(("POST", "/md", (500, {"error": "no markdown"})))
    else:
        routes.append(("POST", "/md", (md_status, md)))
    routes.append(
        (
            "POST",
            "/crawl",
            (crawl_status, crawl if crawl is not None else load("crawl4ai_crawl.json")),
        )
    )
    return replay_transport(routes)


def install(monkeypatch, transport: httpx.MockTransport) -> None:
    """Routes every httpx.AsyncClient(...) in the gateway through *transport*.

    Always subclasses the REAL AsyncClient captured at import: installs nest
    (a second install in one test replaces the first) because patching
    ``gw_server.httpx.AsyncClient`` rebinds the global httpx module attribute.
    """
    import gateway.server as gw_server

    class _ReplayClient(_REAL_CLIENT):
        def __init__(self, *args: Any, **kwargs: Any):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(gw_server.httpx, "AsyncClient", _ReplayClient)


@contextlib.contextmanager
def routed(transport: httpx.MockTransport):
    """Standalone (non-monkeypatch) variant for the fixture generator."""
    import gateway.server as gw_server

    previous = gw_server.httpx.AsyncClient

    class _ReplayClient(_REAL_CLIENT):
        def __init__(self, *args: Any, **kwargs: Any):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    gw_server.httpx.AsyncClient = _ReplayClient
    try:
        yield
    finally:
        gw_server.httpx.AsyncClient = previous
