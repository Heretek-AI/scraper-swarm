"""MCP Gateway server implementing Streamable HTTP transport and scoped tools.

Phase 03-opencode-integration (P2 OpenCode v2 live integration):
- file:///home/john/.gemini/antigravity-cli/brain/d3380741-a97f-484b-8060-be5ef9374790/scraper_swarm_phase5_roadmap.md::P2-C1-C2-C3
- file:///home/john/Projects/scraper-swarm/apps/panel-api/src/panel_api/routers/agents.py
- file:///home/john/Projects/scraper-swarm/apps/gateway/src/gateway/server.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import aiosqlite
import httpx
from fastapi import Cookie, FastAPI, Header, HTTPException, Request, status
from mcp.server.mcpserver import MCPServer
from panel_api.audit import AuditLogger
from panel_api.ssrf_guard import (
    audit_target_for_action,
    deny_reason_for_url,
    is_session_expired_fail_closed,
    resolver_fail_closed,
    sanitize_details,
)
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")
CRAWL4AI_URL = os.environ.get("CRAWL4AI_URL", "http://crawl4ai:11235")
DB_PATH = os.environ.get("SWARM_DB_PATH", "/var/lib/scraper-swarm/panel.db")

mcp_server = MCPServer(name="ScraperSwarmGateway")

# Ticket #6 (gateway contract v1): input bounds and response caps.
# - web_search limit is clamped to [WEB_SEARCH_LIMIT_MIN, WEB_SEARCH_LIMIT_MAX].
# - Fetched/markdown result bytes are capped at MAX_FETCH_BYTES (default 3 MiB,
#   override with SWARM_MAX_FETCH_BYTES); over-cap results are truncated and
#   marked so automated clients can detect it. Documented in
#   docs/opencode-integration.md §5.
WEB_SEARCH_LIMIT_MIN = 1
WEB_SEARCH_LIMIT_MAX = 20
_DEFAULT_MAX_FETCH_BYTES = 3 * 1024 * 1024


def _max_fetch_bytes() -> int:
    try:
        return max(1, int(os.environ.get("SWARM_MAX_FETCH_BYTES", str(_DEFAULT_MAX_FETCH_BYTES))))
    except ValueError:
        return _DEFAULT_MAX_FETCH_BYTES


MAX_FETCH_BYTES = _max_fetch_bytes()


def _cap_text(text: str, limit: int | None = None) -> str:
    """Caps result text to *limit* bytes (default MAX_FETCH_BYTES), marking truncation."""
    cap = MAX_FETCH_BYTES if limit is None else limit
    raw = text.encode("utf-8", errors="ignore")
    if len(raw) <= cap:
        return text
    cut = raw[:cap].decode("utf-8", errors="ignore")
    return f"{cut}\n…[truncated: showing {cap} of {len(raw)} bytes]"


def _clamp_limit(limit: object) -> int:
    """Bounds a web_search limit to [WEB_SEARCH_LIMIT_MIN, WEB_SEARCH_LIMIT_MAX]."""
    try:
        value = int(limit)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = WEB_SEARCH_LIMIT_MIN
    return max(WEB_SEARCH_LIMIT_MIN, min(value, WEB_SEARCH_LIMIT_MAX))


async def _ssrf_precheck(url: str) -> str | None:
    """Runs the SSRF pre-check off the event loop; resolver outage fails closed
    by default (SWARM_SSRF_RESOLVER_FAIL_CLOSED opt-out)."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(deny_reason_for_url, url), timeout=8.0)
    except Exception:
        if resolver_fail_closed():
            return "DNS resolution unavailable (fail-closed)"
        return None


# Phase 03: in-memory sliding-window rate limiter per agent-key id.
# Window is 60s; each key's timestamps are pruned on check. This is a
# single-process guard (documents the rpm contract); operators needing
# multi-replica enforcement put a shared bucket in Valkey (see docs note).
_RATE_BUCKETS: dict[str, list[float]] = {}
# Bound on distinct bucket keys so a cardinality flood cannot grow memory
# without limit; overflow triggers a sweep of expired windows.
_MAX_RATE_BUCKETS = 5000


def _check_rate_limit(key_id: str, rpm: int) -> None:
    """Raises HTTP 429 when key_id exceeds rpm requests in the trailing 60s window."""
    now = time.monotonic()
    window_start = now - 60.0
    hits = [t for t in _RATE_BUCKETS.get(key_id, []) if t > window_start]
    if len(hits) >= max(1, rpm):
        # Persist the pruned window even on deny so the bucket cannot grow
        # with stale timestamps, then bound total cardinality.
        _RATE_BUCKETS[key_id] = hits
        _prune_rate_buckets(window_start)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded ({rpm} req/min)",
        )
    hits.append(now)
    _RATE_BUCKETS[key_id] = hits
    _prune_rate_buckets(window_start)


def _prune_rate_buckets(window_start: float) -> None:
    """Bounds bucket cardinality by sweeping expired windows on every path."""
    if len(_RATE_BUCKETS) <= _MAX_RATE_BUCKETS:
        return
    for bucket_key in list(_RATE_BUCKETS.keys()):
        remaining = [t for t in _RATE_BUCKETS[bucket_key] if t > window_start]
        if remaining:
            _RATE_BUCKETS[bucket_key] = remaining
        else:
            del _RATE_BUCKETS[bucket_key]
    while len(_RATE_BUCKETS) > _MAX_RATE_BUCKETS:
        _RATE_BUCKETS.pop(next(iter(_RATE_BUCKETS)))


def _reset_rate_limits() -> None:
    """Test hook: clears all sliding-window buckets (used between test cases)."""
    _RATE_BUCKETS.clear()
    _DENIED_AUDIT_LAST.clear()


# Phase 03 retry2 QA-B P0-3: denied-row flood guard. Every 401/403/429/
# unknown-tool previously wrote an unbounded audit row (25 bad -> +25 rows,
# 429s keep appending during a flood). Policy: sample denied audits per
# (agent, action, status) with a cooldown window. First denied per key logs
# immediately (forensics preserved); repeats within the window are dropped
# (counter in memory only, no disk growth). Legitimate distinct denials
# (different action/status/agent) still log. Documented in
# docs/opencode-integration.md §5. Cooldown is deliberately short so a
# follow-up forensic probe after the window still leaves a trace.
_DENIED_AUDIT_COOLDOWN_S = 10.0
_DENIED_AUDIT_LAST: dict[str, float] = {}
_MAX_DENIED_AUDIT_KEYS = 5000


def _denied_audit_key(agent_name: str | None, action: str, details: dict | None) -> str:
    status_code = (details or {}).get("status", (details or {}).get("code", "?"))
    return f"{agent_name or 'unknown'}:{action}:{status_code}"


def _should_audit_denied(agent_name: str | None, action: str, details: dict | None) -> bool:
    """Returns True when this denied call should write an audit row."""
    now = time.monotonic()
    key = _denied_audit_key(agent_name, action, details)
    last = _DENIED_AUDIT_LAST.get(key, 0.0)
    if now - last < _DENIED_AUDIT_COOLDOWN_S:
        return False
    _DENIED_AUDIT_LAST[key] = now
    if len(_DENIED_AUDIT_LAST) > _MAX_DENIED_AUDIT_KEYS:
        cutoff = now - _DENIED_AUDIT_COOLDOWN_S
        for k, ts in list(_DENIED_AUDIT_LAST.items()):
            if ts < cutoff:
                del _DENIED_AUDIT_LAST[k]
        while len(_DENIED_AUDIT_LAST) > _MAX_DENIED_AUDIT_KEYS:
            _DENIED_AUDIT_LAST.pop(next(iter(_DENIED_AUDIT_LAST)))
    return True


def _reset_denied_audit() -> None:
    """Test hook: clears the denied-audit cooldown map."""
    _DENIED_AUDIT_LAST.clear()


async def log_agent_activity(
    agent_name: str, action: str, target: str | None = None, details: dict[str, Any] | None = None
) -> None:
    """Logs agent tool execution with cryptographic hash-chaining in the SQLite audit log.

    P1-live-smoke audit fix: the gateway mounts ``swarm_data`` read-write so
    live /mcp calls actually persist ``agent:*`` rows; failures are surfaced
    as warnings (never silent debug) and details are sanitized so the
    no-secrets invariant holds while rows are written.

    Phase 03 retry1 QA-B: search/research query text is preserved verbatim
    (via :func:`audit_target_for_action`) for forensics; only true URLs are
    redacted. Denied calls (401/403/429/unknown-tool) are logged with the
    same sanitization so failures leave a trace.
    """
    if not os.path.exists(DB_PATH):
        log.warning("Agent activity audit skipped: database path %s missing", DB_PATH)
        return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            logger = AuditLogger(db)
            await logger.log(
                actor=f"agent:{agent_name}",
                action=action,
                target=audit_target_for_action(action, target) if target else None,
                details=sanitize_details(details),
            )
    except Exception as e:
        log.warning("Agent activity audit logging failed for %s/%s: %s", agent_name, action, e)


async def _audit_denied(
    agent_name: str | None,
    action: str,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Best-effort denied-call audit row; never raises (must not mask the error).

    Phase 03 retry2 QA-B P0-3: cooldown-sampled so a credential-stuffing or
    429 flood cannot fill the disk (first per 10s per agent/action/status
    writes; repeats in-window are dropped with a debug log).
    """
    try:
        if not _should_audit_denied(agent_name, action, details):
            log.debug(
                "Denied-call audit sampled out for %s/%s (flood guard)",
                agent_name or "unknown",
                action,
            )
            return
        await log_agent_activity(agent_name or "unknown", action, target=target, details=details)
    except Exception as e:
        log.warning("Denied-call audit failed for %s: %s", action, e)


async def _ensure_gateway_session_schema(db: aiosqlite.Connection) -> None:
    """Best-effort legacy migration for old DBs missing sessions.expires_at.

    Phase 03 retry2 QA-B P0-1: old DBs must never 500. Missing column is
    added, legacy NULL/'' rows are swept to expired (fail-closed 401).
    Never raises.
    """
    try:
        async with db.execute("PRAGMA table_info(sessions)") as cur:
            cols = [r["name"] for r in await cur.fetchall()]
        if cols and "expires_at" not in cols:
            await db.execute("ALTER TABLE sessions ADD COLUMN expires_at TIMESTAMP")
            await db.commit()
            async with db.execute("PRAGMA table_info(sessions)") as cur:
                cols = [r["name"] for r in await cur.fetchall()]
        if "expires_at" in cols:
            await db.execute(
                "UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'"
                " WHERE expires_at IS NULL OR TRIM(expires_at) = ''"
            )
            await db.commit()
    except Exception as e:
        log.debug("Gateway session schema migration skipped: %s", e)


async def _ensure_gateway_agent_key_schema(db: aiosqlite.Connection) -> None:
    """Best-effort legacy migration for old DBs missing agent_keys.expires_at."""
    try:
        async with db.execute("PRAGMA table_info(agent_keys)") as cur:
            cols = [r["name"] for r in await cur.fetchall()]
        if cols and "expires_at" not in cols:
            await db.execute("ALTER TABLE agent_keys ADD COLUMN expires_at TIMESTAMP")
            await db.commit()
    except Exception as e:
        log.debug("Gateway agent-key schema migration skipped: %s", e)


async def verify_agent_token(
    auth_header: str | None = None,
    cookie_token: str | None = None,
    required_scope: str = "search",
    enforce_rate_limit: bool = True,
) -> dict[str, Any]:
    """Validates Bearer token against agent_keys or session cookie against sessions.

    Phase 03: expired keys -> 401; missing scope -> 403; over-rpm keys -> 429
    via a per-key 60s sliding window. Per-tool re-verifications within one
    request pass ``enforce_rate_limit=False`` so a single HTTP call costs one
    rate-limit hit.
    """
    # Allow scope to be passed as second positional argument if needed
    if cookie_token in ("search", "scrape", "admin"):
        required_scope = cookie_token
        cookie_token = None

    if not os.path.exists(DB_PATH):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable for authentication",
        )

    # 1. Bearer Token Authentication
    if auth_header and auth_header.startswith("Bearer "):
        raw_token = auth_header.removeprefix("Bearer ").strip()
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            row = None
            try:
                async with db.execute(
                    "SELECT id, name, scopes, rate_limit_rpm, expires_at "
                    "FROM agent_keys WHERE key_hash = ?",
                    (token_hash,),
                ) as cur:
                    row = await cur.fetchone()
            except Exception as e:
                # Phase 03 retry2 QA-B P0-1: old DBs missing expires_at must
                # never 500 — migrate once, retry once, else fail-closed 401.
                if "no such column" in str(e).lower():
                    await _ensure_gateway_agent_key_schema(db)
                    try:
                        async with db.execute(
                            "SELECT id, name, scopes, rate_limit_rpm, expires_at "
                            "FROM agent_keys WHERE key_hash = ?",
                            (token_hash,),
                        ) as cur:
                            row = await cur.fetchone()
                    except Exception:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid or revoked agent API key",
                        ) from None
                else:
                    log.warning("Agent-key lookup failed: %s", e)
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid or revoked agent API key",
                    ) from None
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or revoked agent API key",
                )

            # Phase 03 AC5 + retry2 QA-B P0-1: expired keys fail closed with
            # 401 (same as revoked). NULL (acked never-expire) stays valid;
            # ''/whitespace/unparseable fail closed rather than granting access.
            try:
                expires_at = row["expires_at"]
            except (KeyError, IndexError):
                expires_at = None
            if expires_at is not None:
                exp_text = str(expires_at).strip()
                if not exp_text:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Agent API key expiry invalid",
                    )
                try:
                    exp = datetime.fromisoformat(exp_text)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=UTC)
                    if exp <= datetime.now(UTC):
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Agent API key expired",
                        )
                except HTTPException:
                    raise
                except Exception:
                    # Unparseable expiry fails closed rather than granting access.
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Agent API key expiry invalid",
                    ) from None

            scopes = json.loads(row["scopes"])
            if required_scope not in scopes and "admin" not in scopes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Key lacks required scope '{required_scope}'",
                )

            # Phase 03 AC5: sliding-window per-key rate limit (HTTP 429).
            # Skipped for in-request per-tool re-verification (one HTTP call = one hit).
            if enforce_rate_limit:
                _check_rate_limit(str(row["id"]), int(row["rate_limit_rpm"] or 60))

            return dict(row)

    # 2. Session Cookie Authentication (for web panel operators / admins)
    session_token = cookie_token
    if not session_token and auth_header and auth_header.startswith("Session "):
        session_token = auth_header.removeprefix("Session ").strip()

    if session_token:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            # Phase 03 retry2 QA-B P0-1: migrate-then-read so old DBs without
            # sessions.expires_at never 500 (fail-closed 401 instead).
            await _ensure_gateway_session_schema(db)
            try:
                async with db.execute(
                    "SELECT user_id, username, role, expires_at FROM sessions WHERE token = ?",
                    (session_token,),
                ) as cur:
                    row = await cur.fetchone()
            except Exception as e:
                if "no such column" in str(e).lower():
                    await _ensure_gateway_session_schema(db)
                    try:
                        async with db.execute(
                            "SELECT user_id, username, role, expires_at "
                            "FROM sessions WHERE token = ?",
                            (session_token,),
                        ) as cur:
                            row = await cur.fetchone()
                    except Exception:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required (Bearer token or session cookie)",
                        ) from None
                else:
                    log.warning("Session lookup failed: %s", e)
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Authentication required (Bearer token or session cookie)",
                    ) from None
            if row:
                # Phase 03 retry2 QA-B P0-1: NULL/''/missing/expired sessions
                # fail closed with 401 (never immortal).
                try:
                    sess_expires = row["expires_at"]
                except (KeyError, IndexError):
                    sess_expires = None
                if is_session_expired_fail_closed(sess_expires):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Session expired",
                    )
                role = row["role"]
                if role in ("admin", "operator"):
                    # Phase 03 retry1 QA-B P0-2: session path enforces the
                    # same sliding-window bucket (keyed by session user)
                    # so cookie auth cannot bypass rpm limits.
                    if enforce_rate_limit:
                        _check_rate_limit(f"session:{row['user_id']}", 600)
                    return {
                        "id": row["user_id"],
                        "name": row["username"],
                        "scopes": ["admin", "search", "scrape"],
                        "rate_limit_rpm": 600,
                        "role": role,
                    }
                elif role == "viewer":
                    if required_scope == "search":
                        if enforce_rate_limit:
                            _check_rate_limit(f"session:{row['user_id']}", 60)
                        return {
                            "id": row["user_id"],
                            "name": row["username"],
                            "scopes": ["search"],
                            "rate_limit_rpm": 60,
                            "role": role,
                        }
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Viewer role lacks scrape permissions",
                    )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required (Bearer token or session cookie)",
    )


@mcp_server.tool(name="web_search", description="Search the web using SearXNG meta-search engine.")
async def web_search(query: str, limit: int = 5) -> str:
    """Performs web search via SearXNG JSON endpoint.

    P1 Live Engine Stack Smoke Test (Phase 01-live-smoke): live path
    Agent -> /mcp -> SearXNG -> Smokescreen -> Internet.
    Evidence: scraper_swarm_phase5_roadmap.md::P1-live-smoke-B1-B2,
    walkthrough.md::smoke-3-3, workbench_fix_walkthrough.md::mcp-proof.

    Ticket #6: *limit* is clamped to [1, 20] so an automated client cannot
    demand unbounded engine work per call.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = WEB_SEARCH_LIMIT_MIN
    limit = max(WEB_SEARCH_LIMIT_MIN, min(limit, WEB_SEARCH_LIMIT_MAX))
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])[:limit]
            formatted = []
            for r in results:
                formatted.append(
                    f"Title: {r.get('title')}\nURL: {r.get('url')}\n"
                    f"Snippet: {r.get('content')}\n---"
                )
            return "\n".join(formatted) if formatted else "No results found."
        except Exception as e:
            return f"Error executing search on {SEARXNG_URL}: {e}"


@mcp_server.tool(
    name="fetch_page",
    description="Scrape and extract markdown content from a webpage using Crawl4AI.",
)
async def fetch_page(url: str) -> str:
    """Scrapes a URL using Crawl4AI REST endpoint.

    P1 Live Engine Stack Smoke Test (Phase 01-live-smoke): live path
    Agent -> /mcp -> Crawl4AI -> Smokescreen -> Internet. SSRF is denied
    fail-closed at this gateway layer for 169.254/127/RFC1918/CGNAT/IPv6
    loopback/unspecified plus decimal/hex/octal/dword encodings *before*
    delegating; CRAWL4AI_ALLOW_INTERNAL_URLS=true in the catalog then only
    ever sees legitimate in-stack peer DNS, while Smokescreen remains the
    per-connection enforcement point for plain DNS names.
    Evidence: scraper_swarm_phase5_roadmap.md::P1-live-smoke-B1-B2,
    walkthrough.md::smoke-3-3, workbench_fix_walkthrough.md::mcp-proof.

    Ticket #6: a resolver outage/timeout fails closed by default
    (SWARM_SSRF_RESOLVER_FAIL_CLOSED opt-out) instead of delegating blindly.
    """
    try:
        denial = await asyncio.wait_for(asyncio.to_thread(deny_reason_for_url, url), timeout=8.0)
    except Exception:
        denial = "DNS resolution unavailable (fail-closed)" if resolver_fail_closed() else None
    if denial is not None:
        return f"SSRF denied (fail-closed): {denial}"
    async with httpx.AsyncClient(timeout=45.0) as client:
        try:
            # 1. Try Crawl4AI /md endpoint
            resp = await client.post(
                f"{CRAWL4AI_URL}/md",
                json={"url": url},
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    return data.get("markdown", data.get("html", str(data)))
                return str(data)

            # 2. Fallback to /crawl endpoint
            resp = await client.post(
                f"{CRAWL4AI_URL}/crawl",
                json={"urls": [url]},
            )
            resp.raise_for_status()
            data = resp.json()
            if "results" in data and data["results"]:
                first = data["results"][0]
                return first.get("markdown", first.get("html", "No content extracted."))
            return str(data)
        except Exception as e:
            return f"Error crawling page via {CRAWL4AI_URL}: {e}"


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=WEB_SEARCH_LIMIT_MIN, le=WEB_SEARCH_LIMIT_MAX)


class FetchRequest(BaseModel):
    url: str


def create_gateway_app() -> FastAPI:
    app = FastAPI(title="Scraper Swarm MCP Gateway", version="0.1.0")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/mcp")
    async def mcp_handler(
        request: Request,
        authorization: str | None = Header(None),
        swarm_session: str | None = Cookie(None),
    ):
        """Authenticated MCP Streamable HTTP endpoint supporting JSON-RPC 2.0."""
        cookie_val = swarm_session or request.cookies.get("swarm_session")

        body: dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception as e:
            log.debug("Failed to parse request JSON: %s", e)

        method = body.get("method")
        rpc_id = body.get("id", 1)
        _attempted_tool: str | None = None
        if isinstance(body.get("params"), dict):
            _attempted_tool = body["params"].get("name")

        try:
            agent = await verify_agent_token(
                auth_header=authorization,
                cookie_token=cookie_val,
                required_scope="search",
            )
        except HTTPException as e:
            if e.status_code in (401, 403, 429):
                await _audit_denied(
                    None,
                    "auth_denied",
                    target=_attempted_tool or method,
                    details={
                        "status": e.status_code,
                        "method": method,
                        "tool": _attempted_tool,
                    },
                )
            raise

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ScraperSwarmGateway", "version": "0.1.0"},
                },
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "tools": [
                        {
                            "name": "web_search",
                            "description": "Search the web using SearXNG meta-search engine.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "description": "Search keywords"},
                                    "limit": {
                                        "type": "integer",
                                        "description": "Max results",
                                        "default": 5,
                                    },
                                },
                                "required": ["query"],
                            },
                        },
                        {
                            "name": "fetch_page",
                            "description": (
                                "Scrape and extract markdown content from a webpage using Crawl4AI."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "url": {
                                        "type": "string",
                                        "description": "Target webpage URL to crawl",
                                    },
                                },
                                "required": ["url"],
                            },
                        },
                        {
                            "name": "deep_research",
                            "description": (
                                "Conduct autonomous deep web research and synthesis "
                                "using GPT Researcher."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "Research question or topic",
                                    },
                                },
                                "required": ["query"],
                            },
                        },
                        {
                            "name": "stealth_scrape",
                            "description": (
                                "Extract content from anti-bot protected sites using "
                                "Scrapling Camoufox stealth engine."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "url": {"type": "string", "description": "Target webpage URL"},
                                },
                                "required": ["url"],
                            },
                        },
                    ]
                },
            }

        elif method == "tools/call":
            params = body.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})

            if name == "web_search":
                q = args.get("query", "")
                limit = _clamp_limit(args.get("limit", 5))
                await log_agent_activity(
                    agent["name"], "web_search", target=q, details={"limit": limit}
                )
                result = await web_search(query=q, limit=limit)
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": _cap_text(result)}]},
                }

            elif name == "fetch_page":
                try:
                    await verify_agent_token(
                        auth_header=authorization,
                        cookie_token=cookie_val,
                        required_scope="scrape",
                        enforce_rate_limit=False,
                    )
                except HTTPException as e:
                    if e.status_code in (401, 403, 429):
                        await _audit_denied(
                            str(agent.get("name", "unknown")),
                            "fetch_page_denied",
                            target=args.get("url", ""),
                            details={"status": e.status_code, "reason": e.detail},
                        )
                    raise
                target_url = args.get("url", "")
                # Ticket #6: handler-level pre-check (defense in depth; the
                # tool function checks again before delegating to Crawl4AI).
                denial = await _ssrf_precheck(target_url)
                if denial is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "content": [
                                {"type": "text", "text": f"SSRF denied (fail-closed): {denial}"}
                            ]
                        },
                    }
                await log_agent_activity(agent["name"], "fetch_page", target=target_url)
                result = await fetch_page(url=target_url)
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": _cap_text(result)}]},
                }

            elif name == "deep_research":
                try:
                    await verify_agent_token(
                        auth_header=authorization,
                        cookie_token=cookie_val,
                        required_scope="search",
                        enforce_rate_limit=False,
                    )
                except HTTPException as e:
                    if e.status_code in (401, 403, 429):
                        await _audit_denied(
                            str(agent.get("name", "unknown")),
                            "deep_research_denied",
                            target=args.get("query", ""),
                            details={"status": e.status_code, "reason": e.detail},
                        )
                    raise
                q = args.get("query", "")
                await log_agent_activity(agent["name"], "deep_research", target=q)
                async with httpx.AsyncClient(timeout=120.0) as client:
                    try:
                        r = await client.post(
                            "http://gpt-researcher:8000/research", json={"query": q}
                        )
                        result = r.text
                    except Exception as e:
                        result = f"GPT Researcher error: {e}"
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": _cap_text(result)}]},
                }

            elif name == "stealth_scrape":
                try:
                    await verify_agent_token(
                        auth_header=authorization,
                        cookie_token=cookie_val,
                        required_scope="scrape",
                        enforce_rate_limit=False,
                    )
                except HTTPException as e:
                    if e.status_code in (401, 403, 429):
                        await _audit_denied(
                            str(agent.get("name", "unknown")),
                            "stealth_scrape_denied",
                            target=args.get("url", ""),
                            details={"status": e.status_code, "reason": e.detail},
                        )
                    raise
                target_url = args.get("url", "")
                # Ticket #6: stealth_scrape previously skipped the SSRF
                # pre-check entirely and went straight to Scrapling, letting an
                # internal/peer URL bypass the egress proxy via NO_PROXY.
                # Deny here (and the Scrapling path is never contacted).
                denial = await _ssrf_precheck(target_url)
                if denial is not None:
                    return {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "content": [
                                {"type": "text", "text": f"SSRF denied (fail-closed): {denial}"}
                            ]
                        },
                    }
                await log_agent_activity(agent["name"], "stealth_scrape", target=target_url)
                async with httpx.AsyncClient(timeout=30.0) as client:
                    try:
                        r = await client.post(
                            "http://scrapling:8000/fetch", json={"url": target_url}
                        )
                        result = r.text
                    except Exception as e:
                        result = f"Scrapling error: {e}"
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": _cap_text(result)}]},
                }

            else:
                await _audit_denied(
                    str(agent.get("name", "unknown")),
                    "unknown_tool",
                    target=str(name) if name else method,
                    details={"code": -32601, "tool": name},
                )
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32601, "message": f"Method {name} not found"},
                }

        # Fallback probe for status or handshake check
        return {"status": "connected", "tools": ["web_search", "fetch_page"]}

    @app.post("/api/search")
    async def rest_search(
        req: SearchRequest,
        request: Request,
        authorization: str | None = Header(None),
        swarm_session: str | None = Cookie(None),
    ):
        """REST search endpoint for direct agent querying."""
        cookie_val = swarm_session or request.cookies.get("swarm_session")
        try:
            agent = await verify_agent_token(
                auth_header=authorization,
                cookie_token=cookie_val,
                required_scope="search",
            )
        except HTTPException as e:
            if e.status_code in (401, 403, 429):
                await _audit_denied(
                    None,
                    "auth_denied",
                    target=req.query,
                    details={"status": e.status_code, "endpoint": "/api/search"},
                )
            raise
        await log_agent_activity(
            agent["name"], "web_search", target=req.query, details={"limit": req.limit}
        )
        result = await web_search(query=req.query, limit=req.limit)
        return {"query": req.query, "result": _cap_text(result)}

    @app.post("/api/fetch")
    async def rest_fetch(
        req: FetchRequest,
        request: Request,
        authorization: str | None = Header(None),
        swarm_session: str | None = Cookie(None),
    ):
        """REST page fetch endpoint for direct agent scraping."""
        cookie_val = swarm_session or request.cookies.get("swarm_session")
        try:
            agent = await verify_agent_token(
                auth_header=authorization,
                cookie_token=cookie_val,
                required_scope="scrape",
            )
        except HTTPException as e:
            if e.status_code in (401, 403, 429):
                await _audit_denied(
                    None,
                    "auth_denied",
                    target=req.url,
                    details={"status": e.status_code, "endpoint": "/api/fetch"},
                )
            raise
        await log_agent_activity(agent["name"], "fetch_page", target=req.url)
        result = await fetch_page(url=req.url)
        return {"url": req.url, "result": _cap_text(result)}

    return app


app = create_gateway_app()
