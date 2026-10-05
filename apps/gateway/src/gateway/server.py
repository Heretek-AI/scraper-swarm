"""MCP Gateway server implementing Streamable HTTP transport and scoped tools."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import aiosqlite
import httpx
from fastapi import Cookie, FastAPI, Header, HTTPException, Request, status
from mcp.server.mcpserver import MCPServer
from panel_api.audit import AuditLogger
from pydantic import BaseModel

log = logging.getLogger(__name__)

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")
CRAWL4AI_URL = os.environ.get("CRAWL4AI_URL", "http://crawl4ai:11235")
DB_PATH = os.environ.get("SWARM_DB_PATH", "/var/lib/scraper-swarm/panel.db")

mcp_server = MCPServer(name="ScraperSwarmGateway")


async def log_agent_activity(
    agent_name: str, action: str, target: str | None = None, details: dict[str, Any] | None = None
) -> None:
    """Logs agent tool execution with cryptographic hash-chaining in the SQLite audit log."""
    if not os.path.exists(DB_PATH):
        return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            logger = AuditLogger(db)
            await logger.log(
                actor=f"agent:{agent_name}",
                action=action,
                target=target,
                details=details,
            )
    except Exception as e:
        log.debug("Agent activity audit logging failed: %s", e)


async def verify_agent_token(
    auth_header: str | None = None,
    cookie_token: str | None = None,
    required_scope: str = "search",
) -> dict[str, Any]:
    """Validates Bearer token against agent_keys or session cookie against sessions."""
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
            async with db.execute(
                "SELECT id, name, scopes, rate_limit_rpm FROM agent_keys WHERE key_hash = ?",
                (token_hash,),
            ) as cur:
                row = await cur.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid or revoked agent API key",
                    )

                scopes = json.loads(row["scopes"])
                if required_scope not in scopes and "admin" not in scopes:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Key lacks required scope '{required_scope}'",
                    )

                return dict(row)

    # 2. Session Cookie Authentication (for web panel operators / admins)
    session_token = cookie_token
    if not session_token and auth_header and auth_header.startswith("Session "):
        session_token = auth_header.removeprefix("Session ").strip()

    if session_token:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT user_id, username, role, expires_at FROM sessions WHERE token = ?",
                (session_token,),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    role = row["role"]
                    if role in ("admin", "operator"):
                        return {
                            "id": row["user_id"],
                            "name": row["username"],
                            "scopes": ["admin", "search", "scrape"],
                            "rate_limit_rpm": 600,
                            "role": role,
                        }
                    elif role == "viewer":
                        if required_scope == "search":
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
    """
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
    Agent -> /mcp -> Crawl4AI -> Smokescreen -> Internet. SSRF is delegated
    fail-closed to Smokescreen (CRAWL4AI_ALLOW_INTERNAL_URLS=true in the
    catalog; the renderer forces HTTP(S)_PROXY=http://egress-web:4750).
    Evidence: scraper_swarm_phase5_roadmap.md::P1-live-smoke-B1-B2,
    walkthrough.md::smoke-3-3, workbench_fix_walkthrough.md::mcp-proof.
    """
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
    limit: int = 5


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
        agent = await verify_agent_token(
            auth_header=authorization,
            cookie_token=cookie_val,
            required_scope="search",
        )

        body = {}
        try:
            body = await request.json()
        except Exception as e:
            log.debug("Failed to parse request JSON: %s", e)

        method = body.get("method")
        rpc_id = body.get("id", 1)

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
                limit = args.get("limit", 5)
                await log_agent_activity(
                    agent["name"], "web_search", target=q, details={"limit": limit}
                )
                result = await web_search(query=q, limit=limit)
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": result}]},
                }

            elif name == "fetch_page":
                await verify_agent_token(
                    auth_header=authorization,
                    cookie_token=cookie_val,
                    required_scope="scrape",
                )
                target_url = args.get("url", "")
                await log_agent_activity(agent["name"], "fetch_page", target=target_url)
                result = await fetch_page(url=target_url)
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": result}]},
                }

            elif name == "deep_research":
                await verify_agent_token(
                    auth_header=authorization,
                    cookie_token=cookie_val,
                    required_scope="search",
                )
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
                    "result": {"content": [{"type": "text", "text": result}]},
                }

            elif name == "stealth_scrape":
                await verify_agent_token(
                    auth_header=authorization,
                    cookie_token=cookie_val,
                    required_scope="scrape",
                )
                target_url = args.get("url", "")
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
                    "result": {"content": [{"type": "text", "text": result}]},
                }

            else:
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
        agent = await verify_agent_token(
            auth_header=authorization,
            cookie_token=cookie_val,
            required_scope="search",
        )
        await log_agent_activity(
            agent["name"], "web_search", target=req.query, details={"limit": req.limit}
        )
        result = await web_search(query=req.query, limit=req.limit)
        return {"query": req.query, "result": result}

    @app.post("/api/fetch")
    async def rest_fetch(
        req: FetchRequest,
        request: Request,
        authorization: str | None = Header(None),
        swarm_session: str | None = Cookie(None),
    ):
        """REST page fetch endpoint for direct agent scraping."""
        cookie_val = swarm_session or request.cookies.get("swarm_session")
        agent = await verify_agent_token(
            auth_header=authorization,
            cookie_token=cookie_val,
            required_scope="scrape",
        )
        await log_agent_activity(agent["name"], "fetch_page", target=req.url)
        result = await fetch_page(url=req.url)
        return {"url": req.url, "result": result}

    return app


app = create_gateway_app()
