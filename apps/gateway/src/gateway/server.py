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
from typing import Any, Literal, cast

import aiosqlite
import httpx
from fastapi import Cookie, FastAPI, Header, HTTPException, Request, Response, status
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

from gateway import __version__ as GATEWAY_VERSION

log = logging.getLogger(__name__)

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")
CRAWL4AI_URL = os.environ.get("CRAWL4AI_URL", "http://crawl4ai:11235")
SCRAPLING_URL = os.environ.get("SCRAPLING_URL", "http://scrapling:8000")
GPT_RESEARCHER_URL = os.environ.get("GPT_RESEARCHER_URL", "http://gpt-researcher:8000")
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
        # Ticket #5: 429 carries Retry-After (seconds until the oldest hit
        # slides out of the window); the envelope equivalent is
        # {code: rate_limited, retry_after_s}.
        oldest = hits[0] if hits else now
        retry_after = max(1, int(60.0 - (now - oldest)) + 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded ({rpm} req/min)",
            headers={"Retry-After": str(retry_after)},
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
    _LAST_USED_TOUCH.clear()


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
    """Best-effort legacy migration for old DBs missing agent_keys columns."""
    try:
        async with db.execute("PRAGMA table_info(agent_keys)") as cur:
            cols = [r["name"] for r in await cur.fetchall()]
        if cols and "expires_at" not in cols:
            await db.execute("ALTER TABLE agent_keys ADD COLUMN expires_at TIMESTAMP")
            await db.commit()
        # Ticket #7: legacy DBs predate last_used_at (service-account use).
        async with db.execute("PRAGMA table_info(agent_keys)") as cur:
            cols = [r["name"] for r in await cur.fetchall()]
        if cols and "last_used_at" not in cols:
            await db.execute("ALTER TABLE agent_keys ADD COLUMN last_used_at TIMESTAMP")
            await db.commit()
    except Exception as e:
        log.debug("Gateway agent-key schema migration skipped: %s", e)


# Ticket #7: last_used_at is refreshed at most once per key per window so a
# high-rpm service key does not turn every request into a DB write.
_LAST_USED_TOUCH: dict[str, float] = {}
_LAST_USED_TOUCH_WINDOW_S = 60.0


async def _maybe_touch_last_used(key_id: str) -> None:
    """Best-effort throttled last_used_at refresh; never raises."""
    try:
        now = time.monotonic()
        if now - _LAST_USED_TOUCH.get(key_id, 0.0) < _LAST_USED_TOUCH_WINDOW_S:
            return
        _LAST_USED_TOUCH[key_id] = now
        if not os.path.exists(DB_PATH):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            try:
                await db.execute(
                    "UPDATE agent_keys SET last_used_at = ? WHERE id = ?",
                    (datetime.now(UTC).isoformat(), key_id),
                )
                await db.commit()
            except Exception as e:
                # Legacy DBs predate the column: migrate once, retry once.
                if "no such column" in str(e).lower():
                    await _ensure_gateway_agent_key_schema(db)
                    try:
                        await db.execute(
                            "UPDATE agent_keys SET last_used_at = ? WHERE id = ?",
                            (datetime.now(UTC).isoformat(), key_id),
                        )
                        await db.commit()
                    except Exception as retry_e:
                        log.debug("last_used_at retry skipped for %s: %s", key_id, retry_e)
                else:
                    log.debug("last_used_at refresh skipped for %s: %s", key_id, e)
    except Exception as e:
        log.debug("last_used_at refresh skipped for %s: %s", key_id, e)


async def verify_agent_token(
    auth_header: str | None = None,
    cookie_token: str | None = None,
    required_scope: str | None = "search",
    enforce_rate_limit: bool = True,
) -> dict[str, Any]:
    """Validates Bearer token against agent_keys or session cookie against sessions.

    Phase 03: expired keys -> 401; missing scope -> 403; over-rpm keys -> 429
    via a per-key 60s sliding window. Per-tool re-verifications within one
    request pass ``enforce_rate_limit=False`` so a single HTTP call costs one
    rate-limit hit.

    Ticket #4: ``required_scope=None`` authenticates any valid key/session
    without a scope check. The /mcp entry uses this so initialize/tools/list
    work for every key while tools/list filters and each tools/call branch
    enforces its own scope.
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

            # Corrupt scopes must fail closed (401), never 500.
            try:
                scopes = json.loads(row["scopes"])
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or revoked agent API key",
                ) from None
            if (
                required_scope is not None
                and required_scope not in scopes
                and "admin" not in scopes
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Key lacks required scope '{required_scope}'",
                )

            # Phase 03 AC5: sliding-window per-key rate limit (HTTP 429).
            # Skipped for in-request per-tool re-verification (one HTTP call = one hit).
            if enforce_rate_limit:
                _check_rate_limit(str(row["id"]), int(row["rate_limit_rpm"] or 60))

            # Ticket #7: throttled last-used refresh for service-account observability.
            await _maybe_touch_last_used(str(row["id"]))

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
                    if required_scope is None or required_scope == "search":
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
async def web_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Performs web search via SearXNG JSON endpoint.

    P1 Live Engine Stack Smoke Test (Phase 01-live-smoke): live path
    Agent -> /mcp -> SearXNG -> Smokescreen -> Internet.
    Evidence: scraper_swarm_phase5_roadmap.md::P1-live-smoke-B1-B2,
    walkthrough.md::smoke-3-3, workbench_fix_walkthrough.md::mcp-proof.

    Ticket #5: returns a contract-v1 dict (text + structured + is_error).
    Ticket #6: *limit* is clamped to [1, 20].
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = WEB_SEARCH_LIMIT_MIN
    limit = max(WEB_SEARCH_LIMIT_MIN, min(limit, WEB_SEARCH_LIMIT_MAX))
    fetched_at = datetime.now(UTC).isoformat()
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                f"{SEARXNG_URL}/search",
                params={"q": query, "format": "json"},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("results", [])[:limit] if isinstance(data, dict) else []
            results = [
                {
                    "rank": i + 1,
                    "title": str(r.get("title") or ""),
                    "url": str(r.get("url") or ""),
                    "snippet": str(r.get("content") or ""),
                    "engine": "searxng",
                }
                for i, r in enumerate(items)
                if isinstance(r, dict)
            ]
            formatted = [
                f"Title: {x['title']}\nURL: {x['url']}\nSnippet: {x['snippet']}\n---"
                for x in results
            ]
            text = "\n".join(formatted) if formatted else "No results found."
            return {
                "text": text,
                "structured": {
                    "query": query,
                    "results": results,
                    "engine": "searxng",
                    "fetched_at": fetched_at,
                },
                "is_error": False,
            }
        except httpx.TimeoutException as e:
            return _tool_error("upstream_timeout", f"SearXNG search timed out: {e}")
        except Exception as e:
            return _tool_error("upstream_error", f"SearXNG search failed: {e}")


@mcp_server.tool(
    name="fetch_page",
    description="Scrape and extract markdown content from a webpage using Crawl4AI.",
)
async def fetch_page(url: str) -> dict[str, Any]:
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

    Ticket #5: returns a contract-v1 dict with final_url, status_code,
    content_type, content_sha256 (over the returned content bytes), and a
    truncation flag.
    Ticket #6: a resolver outage/timeout fails closed by default
    (SWARM_SSRF_RESOLVER_FAIL_CLOSED opt-out) instead of delegating blindly.
    """
    try:
        denial = await asyncio.wait_for(asyncio.to_thread(deny_reason_for_url, url), timeout=8.0)
    except Exception:
        denial = "DNS resolution unavailable (fail-closed)" if resolver_fail_closed() else None
    if denial is not None:
        return _tool_error("ssrf_denied", f"SSRF denied (fail-closed): {denial}")
    async with httpx.AsyncClient(timeout=45.0) as client:
        try:
            # 1. Try Crawl4AI /md endpoint
            data: Any = None
            resp = await client.post(
                f"{CRAWL4AI_URL}/md",
                json={"url": url},
            )
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    data = None

            if data is None:
                # 2. Fallback to /crawl endpoint
                resp = await client.post(
                    f"{CRAWL4AI_URL}/crawl",
                    json={"urls": [url]},
                )
                resp.raise_for_status()
                payload = resp.json()
                if isinstance(payload, dict) and payload.get("results"):
                    first = payload["results"][0]
                    data = first if isinstance(first, dict) else {"text": str(first)}
                else:
                    data = payload
            return _fetch_success(url, data)
        except httpx.TimeoutException as e:
            return _tool_error("upstream_timeout", f"Crawl4AI fetch timed out: {e}")
        except Exception as e:
            return _tool_error("upstream_error", f"Crawl4AI fetch failed: {e}")


def _first_markdown_heading(content: str, format: str) -> str | None:
    if format != "markdown":
        return None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


def _fetch_success(requested_url: str, data: Any) -> dict[str, Any]:
    """Builds the contract-v1 fetch_page result from an engine payload."""
    fetched_at = datetime.now(UTC).isoformat()
    if not isinstance(data, dict):
        data = {"text": str(data)}
    markdown = data.get("markdown")
    html = data.get("html")
    if isinstance(markdown, str) and markdown:
        content, format = markdown, "markdown"
    elif isinstance(html, str) and html:
        content, format = html, "html"
    else:
        content, format = str(data.get("text", str(data))), "text"
    raw = content.encode("utf-8")
    truncated = len(raw) > MAX_FETCH_BYTES
    if truncated:
        content = raw[:MAX_FETCH_BYTES].decode("utf-8", errors="ignore")
        raw = content.encode("utf-8")
    title = data.get("title")
    if not isinstance(title, str) or not title:
        title = _first_markdown_heading(content, format)
    final_url = data.get("final_url") or data.get("url")
    if not isinstance(final_url, str) or not final_url:
        final_url = requested_url
    status_code = data.get("status_code")
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        status_code = 200
    content_type = data.get("content_type")
    if not isinstance(content_type, str):
        content_type = None
    return {
        "text": content,
        "structured": {
            "requested_url": requested_url,
            "final_url": final_url,
            "status_code": status_code,
            "content_type": content_type,
            "title": title,
            "fetched_at": fetched_at,
            "format": format,
            "content": content,
            "content_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "truncated": truncated,
            "engine": "crawl4ai",
        },
        "is_error": False,
    }


async def _probe_engine(base_url: str) -> dict[str, Any]:
    """Probes one engine base URL; any HTTP response counts as reachable."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(base_url)
        return {
            "ok": True,
            "ms": int((time.monotonic() - start) * 1000),
            "status": resp.status_code,
        }
    except Exception as e:
        return {
            "ok": False,
            "ms": int((time.monotonic() - start) * 1000),
            "status": None,
            "error": type(e).__name__,
        }


async def _check_db() -> tuple[bool, str | None]:
    """True/None when the panel DB answers; False/reason otherwise."""
    if not os.path.exists(DB_PATH):
        return False, f"database path {DB_PATH} missing"
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("SELECT 1")
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


class SearchRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=WEB_SEARCH_LIMIT_MIN, le=WEB_SEARCH_LIMIT_MAX)


class FetchRequest(BaseModel):
    url: str


# ---- Ticket #4 (MCP / JSON-RPC 2.0 conformance) --------------------------------
#
# Tool input schemas are defined once as Pydantic models. tools/list serves
# schemas generated from these same models, and tools/call validates arguments
# with them (failures -> JSON-RPC -32602). Behavioural basis: Concord MCP
# jsonrpc-strict.ts (clean-room port: no code copied).
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[-1]


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=WEB_SEARCH_LIMIT_MIN, le=WEB_SEARCH_LIMIT_MAX)


class FetchPageArgs(BaseModel):
    url: str = Field(
        min_length=1,
        description="Target webpage URL to crawl (max 2048 chars; longer URLs are SSRF-denied)",
    )


class DeepResearchArgs(BaseModel):
    query: str = Field(min_length=1)


class StealthScrapeArgs(BaseModel):
    url: str = Field(
        min_length=1,
        description="Target webpage URL (max 2048 chars; longer URLs are SSRF-denied)",
    )


# ---- Ticket #5 (contract v1: structured results, error codes, version) -----
#
# Contract version 1: every /mcp response carries X-Swarm-Contract: 1 and
# initialize reports contractVersion 1. Bump rules: additive changes keep v1;
# breaking changes go to v2 (documented in docs/opencode-integration.md).
CONTRACT_VERSION = 1

# Machine-readable error codes. Transport-level failures keep their HTTP
# mapping (invalid_params -> -32602, scope_denied -> 403, rate_limited ->
# 429 + Retry-After); execution failures ride the isError envelope below.
ERROR_CODES = (
    "invalid_params",
    "ssrf_denied",
    "blocked_by_policy",  # reserved: future policy refusals (e.g. robots enforcement)
    "upstream_error",
    "upstream_timeout",
    "rate_limited",
    "scope_denied",
    "engine_unavailable",
)


class SearchResultItem(BaseModel):
    rank: int
    title: str
    url: str
    snippet: str
    engine: str = "searxng"


class WebSearchStructured(BaseModel):
    query: str
    results: list[SearchResultItem]
    engine: str = "searxng"
    fetched_at: str


class FetchPageStructured(BaseModel):
    requested_url: str
    final_url: str
    status_code: int
    content_type: str | None = None
    title: str | None = None
    fetched_at: str
    format: Literal["markdown", "html", "text"]
    content: str
    content_sha256: str
    bytes: int
    truncated: bool
    engine: str = "crawl4ai"


class ErrorStructured(BaseModel):
    code: str
    message: str
    retry_after_s: int | None = None


def _scrub_internal_urls(text: str) -> str:
    """Replaces engine base URLs with engine names so client-visible errors
    never leak internal hostnames or ports (ticket #5)."""
    for name, base in (
        ("searxng", SEARXNG_URL),
        ("crawl4ai", CRAWL4AI_URL),
        ("scrapling", SCRAPLING_URL),
        ("gpt-researcher", GPT_RESEARCHER_URL),
    ):
        if base:
            text = text.replace(base, f"<{name}>")
    return text


def _tool_error(code: str, message: str, retry_after_s: int | None = None) -> dict[str, Any]:
    """Builds an isError tool result with a machine-readable code."""
    structured: dict[str, Any] = {"code": code, "message": _scrub_internal_urls(str(message))}
    if retry_after_s is not None:
        structured["retry_after_s"] = retry_after_s
    return {
        "text": structured["message"],
        "structured": structured,
        "is_error": True,
        "code": code,
    }


def _render_tool_result(rpc_id: Any, result: Any) -> dict[str, Any]:
    """Renders a tool outcome as JSON-RPC result: legacy text-only for plain
    strings (mocked/older paths), text + structuredContent + isError for
    contract-v1 dict results. The text block is always kept for older clients."""
    if isinstance(result, dict) and "structured" in result:
        out: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "content": [{"type": "text", "text": _cap_text(str(result.get("text", "")))}],
                "structuredContent": result["structured"],
            },
        }
        if result.get("is_error"):
            out["result"]["isError"] = True
        return out
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {"content": [{"type": "text", "text": _cap_text(str(result))}]},
    }


def _engine_unavailable(name: str) -> dict[str, Any] | None:
    """Returns an engine_unavailable error result when the tool's engine is
    not deployed, else None."""
    engine = _TOOL_SPECS[name][2]
    if engine in _deployed_engines():
        return None
    return _tool_error(
        "engine_unavailable", f"Engine '{engine}' for tool '{name}' is not deployed or healthy"
    )


# name -> (arguments model, required scope, backing engine, description)
_TOOL_SPECS: dict[str, tuple[type[BaseModel], str, str, str]] = {
    "web_search": (
        WebSearchArgs,
        "search",
        "searxng",
        "Search the web using SearXNG meta-search engine.",
    ),
    "fetch_page": (
        FetchPageArgs,
        "scrape",
        "crawl4ai",
        "Scrape and extract markdown content from a webpage using Crawl4AI.",
    ),
    "deep_research": (
        DeepResearchArgs,
        "search",
        "gpt-researcher",
        "Conduct autonomous deep web research and synthesis using GPT Researcher.",
    ),
    "stealth_scrape": (
        StealthScrapeArgs,
        "scrape",
        "scrapling",
        "Extract content from anti-bot protected sites using Scrapling Camoufox stealth engine.",
    ),
}


def _deployed_engines() -> set[str]:
    """Engines considered deployed/healthy for tools/list filtering.

    Reads SWARM_DEPLOYED_ENGINES (comma-separated) at call time; unset means
    all engines. Ticket #7's /ready endpoint is the future truth source —
    tools/list will prefer it when available (same helper, same shape).
    """
    raw = os.environ.get("SWARM_DEPLOYED_ENGINES", "")
    engines = {e.strip() for e in raw.split(",") if e.strip()}
    if engines:
        return engines
    return {spec[2] for spec in _TOOL_SPECS.values()}


def _visible_tools(scopes: list[str]) -> list[dict[str, Any]]:
    """Tools the caller may see: required scope held AND engine deployed."""
    deployed = _deployed_engines()
    out: list[dict[str, Any]] = []
    for name, (model, scope, engine, desc) in _TOOL_SPECS.items():
        if scope not in scopes and "admin" not in scopes:
            continue
        if engine not in deployed:
            continue
        out.append({"name": name, "description": desc, "inputSchema": model.model_json_schema()})
        # Ticket #5: outputSchema for the structured tools (deep_research and
        # stealth_scrape carry the error-code envelope only).
        if name == "web_search":
            out[-1]["outputSchema"] = WebSearchStructured.model_json_schema()
        elif name == "fetch_page":
            out[-1]["outputSchema"] = FetchPageStructured.model_json_schema()
    return out


def _rpc_error(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _params_error_detail(exc: Exception) -> str:
    from pydantic import ValidationError as _VE

    if isinstance(exc, _VE):
        parts = []
        for er in exc.errors(include_url=False):
            loc = ".".join(str(p) for p in er.get("loc", ()))
            parts.append(f"{loc or 'arguments'}: {er.get('msg', 'invalid')}")
        return "Invalid params: " + "; ".join(parts[:5])
    return f"Invalid params: {exc}"


def create_gateway_app() -> FastAPI:
    app = FastAPI(title="Scraper Swarm MCP Gateway", version=GATEWAY_VERSION)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.middleware("http")
    async def _contract_version_header(request: Request, call_next):
        """Ticket #5: every /mcp response carries X-Swarm-Contract: 1."""
        response = await call_next(request)
        if request.url.path == "/mcp":
            response.headers["X-Swarm-Contract"] = str(CONTRACT_VERSION)
        return response

    @app.get("/ready")
    async def ready():
        """Deep readiness: DB reachable plus per-engine liveness with latency.

        Ticket #7: operators (and #4's tools/list engine filter, once wired)
        need a truthful readiness picture. Any HTTP response counts the engine
        as reachable; transport errors mark it down. Routed through Caddy
        (deploy/Caddyfile) alongside /health.
        """
        engines: dict[str, dict[str, Any]] = {}
        for name, base in (
            ("searxng", SEARXNG_URL),
            ("crawl4ai", CRAWL4AI_URL),
            ("scrapling", SCRAPLING_URL),
            ("gpt-researcher", GPT_RESEARCHER_URL),
        ):
            engines[name] = await _probe_engine(base)
        db_ok, db_error = await _check_db()
        return {
            "ready": db_ok and all(e["ok"] for e in engines.values()),
            "db": {"ok": db_ok, **({"error": db_error} if db_error else {})},
            "engines": engines,
        }

    @app.post("/mcp")
    async def mcp_handler(
        request: Request,
        authorization: str | None = Header(None),
        swarm_session: str | None = Cookie(None),
    ):
        """Authenticated MCP Streamable HTTP endpoint with strict JSON-RPC 2.0 semantics.

        Ticket #4: malformed JSON -> -32700; wrong version/method shape ->
        -32600; unknown method/tool -> -32601; bad tool arguments -> -32602;
        requests without an id are notifications -> HTTP 202 with no body;
        the id is echoed exactly (never defaulted). Authentication and the
        single per-request rate-limit hit still run first for every call,
        including initialize/tools/list (uniform-cost decision, documented).
        """
        cookie_val = swarm_session or request.cookies.get("swarm_session")

        parse_error = False
        body_is_object = False
        body: dict[str, Any] = {}
        try:
            parsed = await request.json()
        except Exception as e:
            parse_error = True
            log.debug("Failed to parse request JSON: %s", e)
            parsed = None
        if isinstance(parsed, dict):
            body_is_object = True
            body = parsed

        method = body.get("method")
        has_id = "id" in body
        rpc_id = body.get("id")
        _attempted_tool: str | None = None
        if isinstance(body.get("params"), dict):
            _attempted_tool = body["params"].get("name")

        try:
            agent = await verify_agent_token(
                auth_header=authorization,
                cookie_token=cookie_val,
                required_scope=None,
            )
        except HTTPException as e:
            if e.status_code in (401, 403, 429):
                await _audit_denied(
                    None,
                    "auth_denied",
                    target=_attempted_tool or (method if isinstance(method, str) else None),
                    details={
                        "status": e.status_code,
                        "method": method if isinstance(method, str) else None,
                        "tool": _attempted_tool,
                    },
                )
            raise

        if parse_error:
            return _rpc_error(None, -32700, "Parse error: malformed JSON")
        if not body_is_object:
            return _rpc_error(None, -32600, "Invalid Request: body must be a JSON object")
        if body.get("jsonrpc") != "2.0":
            return _rpc_error(rpc_id, -32600, "Invalid Request: jsonrpc must be '2.0'")
        if not isinstance(method, str) or not method:
            return _rpc_error(rpc_id, -32600, "Invalid Request: method must be a string")
        if not has_id:
            # Notification: authenticated, then acknowledged with no body.
            return Response(status_code=202)

        if method == "initialize":
            params = body.get("params")
            if not isinstance(params, dict):
                params = {}
            client_version = params.get("protocolVersion")
            negotiated = (
                client_version
                if client_version in SUPPORTED_PROTOCOL_VERSIONS
                else LATEST_PROTOCOL_VERSION
            )
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "protocolVersion": negotiated,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "ScraperSwarmGateway",
                        "version": GATEWAY_VERSION,
                        "contractVersion": CONTRACT_VERSION,
                    },
                },
            }

        elif method == "tools/list":
            scopes = agent.get("scopes", [])
            if isinstance(scopes, str):
                try:
                    scopes = json.loads(scopes)
                except Exception:
                    scopes = []
            if not isinstance(scopes, list):
                scopes = []
            scopes = [s for s in scopes if isinstance(s, str)]
            return {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {"tools": _visible_tools(list(scopes))},
            }

        elif method == "tools/call":
            params = body.get("params", {})
            if not isinstance(params, dict):
                return _rpc_error(rpc_id, -32602, "Invalid params: params must be an object")
            name = params.get("name")
            spec = _TOOL_SPECS.get(name) if isinstance(name, str) else None
            if spec is None:
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
            args_model, required_scope, _engine, _desc = spec
            raw_args = params.get("arguments", {})
            if not isinstance(raw_args, dict):
                return _rpc_error(rpc_id, -32602, "Invalid params: arguments must be an object")
            if name == "web_search":
                # Ticket #6: clamp the limit before validation so over-range
                # values are bounded (never a validation error); genuinely bad
                # params (missing/wrong-typed query) still yield -32602.
                raw_args = {**raw_args, "limit": _clamp_limit(raw_args.get("limit", 5))}
            try:
                args = args_model.model_validate(raw_args)
            except Exception as e:
                return _rpc_error(rpc_id, -32602, _params_error_detail(e))

            if name == "web_search":
                args = cast(WebSearchArgs, args)
                try:
                    agent = await verify_agent_token(
                        auth_header=authorization,
                        cookie_token=cookie_val,
                        required_scope="search",
                        enforce_rate_limit=False,
                    )
                except HTTPException as e:
                    if e.status_code in (401, 403, 429):
                        await _audit_denied(
                            str(agent.get("name", "unknown")),
                            "web_search_denied",
                            target=args.query,
                            details={"status": e.status_code, "reason": e.detail},
                        )
                    raise
                q = args.query
                limit = _clamp_limit(args.limit)
                unavailable = _engine_unavailable("web_search")
                if unavailable is not None:
                    return _render_tool_result(rpc_id, unavailable)
                await log_agent_activity(
                    agent["name"], "web_search", target=q, details={"limit": limit}
                )
                result = await web_search(query=q, limit=limit)
                return _render_tool_result(rpc_id, result)

            elif name == "fetch_page":
                args = cast(FetchPageArgs, args)
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
                            target=args.url,
                            details={"status": e.status_code, "reason": e.detail},
                        )
                    raise
                target_url = args.url
                # Ticket #6: handler-level pre-check (defense in depth; the
                # tool function checks again before delegating to Crawl4AI).
                denial = await _ssrf_precheck(target_url)
                if denial is not None:
                    return _render_tool_result(
                        rpc_id,
                        _tool_error("ssrf_denied", f"SSRF denied (fail-closed): {denial}"),
                    )
                unavailable = _engine_unavailable("fetch_page")
                if unavailable is not None:
                    return _render_tool_result(rpc_id, unavailable)
                await log_agent_activity(agent["name"], "fetch_page", target=target_url)
                result = await fetch_page(url=target_url)
                return _render_tool_result(rpc_id, result)

            elif name == "deep_research":
                args = cast(DeepResearchArgs, args)
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
                            target=args.query,
                            details={"status": e.status_code, "reason": e.detail},
                        )
                    raise
                q = args.query
                unavailable = _engine_unavailable("deep_research")
                if unavailable is not None:
                    return _render_tool_result(rpc_id, unavailable)
                await log_agent_activity(agent["name"], "deep_research", target=q)
                async with httpx.AsyncClient(timeout=120.0) as client:
                    try:
                        r = await client.post(
                            "http://gpt-researcher:8000/research", json={"query": q}
                        )
                        result = r.text
                    except httpx.TimeoutException as e:
                        return _render_tool_result(
                            rpc_id,
                            _tool_error("upstream_timeout", f"GPT Researcher timed out: {e}"),
                        )
                    except Exception as e:
                        return _render_tool_result(
                            rpc_id,
                            _tool_error("upstream_error", f"GPT Researcher error: {e}"),
                        )
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": _cap_text(result)}]},
                }

            elif name == "stealth_scrape":
                args = cast(StealthScrapeArgs, args)
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
                            target=args.url,
                            details={"status": e.status_code, "reason": e.detail},
                        )
                    raise
                target_url = args.url
                # Ticket #6: stealth_scrape previously skipped the SSRF
                # pre-check entirely and went straight to Scrapling, letting an
                # internal/peer URL bypass the egress proxy via NO_PROXY.
                # Deny here (and the Scrapling path is never contacted).
                denial = await _ssrf_precheck(target_url)
                if denial is not None:
                    return _render_tool_result(
                        rpc_id,
                        _tool_error("ssrf_denied", f"SSRF denied (fail-closed): {denial}"),
                    )
                unavailable = _engine_unavailable("stealth_scrape")
                if unavailable is not None:
                    return _render_tool_result(rpc_id, unavailable)
                await log_agent_activity(agent["name"], "stealth_scrape", target=target_url)
                async with httpx.AsyncClient(timeout=30.0) as client:
                    try:
                        r = await client.post(
                            "http://scrapling:8000/fetch", json={"url": target_url}
                        )
                        result = r.text
                    except httpx.TimeoutException as e:
                        return _render_tool_result(
                            rpc_id,
                            _tool_error("upstream_timeout", f"Scrapling timed out: {e}"),
                        )
                    except Exception as e:
                        return _render_tool_result(
                            rpc_id, _tool_error("upstream_error", f"Scrapling error: {e}")
                        )
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": _cap_text(result)}]},
                }

        # Unknown top-level method (notifications already returned 202 above).
        return _rpc_error(rpc_id, -32601, f"Method {method} not found")

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
        # Ticket #5: the REST shape stays text-only; structure lives on /mcp.
        text = result["text"] if isinstance(result, dict) else str(result)
        return {"query": req.query, "result": _cap_text(text)}

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
        # Ticket #5: the REST shape stays text-only; structure lives on /mcp.
        text = result["text"] if isinstance(result, dict) else str(result)
        return {"url": req.url, "result": _cap_text(text)}

    return app


app = create_gateway_app()
