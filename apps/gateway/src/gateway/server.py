"""MCP Gateway server implementing Streamable HTTP transport and scoped tools."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
import aiosqlite
import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from pydantic import BaseModel
from mcp.server.mcpserver import MCPServer
from panel_api.audit import AuditLogger

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
    except Exception:
        pass


async def verify_agent_token(auth_header: str | None, required_scope: str) -> dict[str, Any]:
    """Validates the Bearer token against SQLite agent_keys and checks scope."""
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Bearer token",
        )

    raw_token = auth_header.removeprefix("Bearer ").strip()
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    if not os.path.exists(DB_PATH):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable for authentication",
        )

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


@mcp_server.tool(name="web_search", description="Search the web using SearXNG meta-search engine.")
async def web_search(query: str, limit: int = 5) -> str:
    """Performs web search via SearXNG JSON endpoint."""
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
                formatted.append(f"Title: {r.get('title')}\nURL: {r.get('url')}\nSnippet: {r.get('content')}\n---")
            return "\n".join(formatted) if formatted else "No results found."
        except Exception as e:
            return f"Error executing search on {SEARXNG_URL}: {e}"


@mcp_server.tool(name="fetch_page", description="Scrape and extract markdown content from a webpage using Crawl4AI.")
async def fetch_page(url: str) -> str:
    """Scrapes a URL using Crawl4AI REST endpoint."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{CRAWL4AI_URL}/crawl",
                json={"urls": [url], "priority": 10},
            )
            resp.raise_for_status()
            data = resp.json()
            # Crawl4AI typically returns task result or markdown
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
    ):
        """Authenticated MCP Streamable HTTP endpoint supporting JSON-RPC 2.0."""
        agent = await verify_agent_token(authorization, required_scope="search")

        body = {}
        try:
            body = await request.json()
        except Exception:
            pass

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
                                    "limit": {"type": "integer", "description": "Max results", "default": 5},
                                },
                                "required": ["query"],
                            },
                        },
                        {
                            "name": "fetch_page",
                            "description": "Scrape and extract markdown content from a webpage using Crawl4AI.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "url": {"type": "string", "description": "Target webpage URL to crawl"},
                                },
                                "required": ["url"],
                            },
                        },
                        {
                            "name": "deep_research",
                            "description": "Conduct autonomous deep web research and synthesis using GPT Researcher.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string", "description": "Research question or topic"},
                                },
                                "required": ["query"],
                            },
                        },
                        {
                            "name": "stealth_scrape",
                            "description": "Extract content from anti-bot protected sites using Scrapling Camoufox stealth engine.",
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
                await log_agent_activity(agent["name"], "web_search", target=q, details={"limit": limit})
                result = await web_search(query=q, limit=limit)
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": result}]},
                }

            elif name == "fetch_page":
                await verify_agent_token(authorization, required_scope="scrape")
                target_url = args.get("url", "")
                await log_agent_activity(agent["name"], "fetch_page", target=target_url)
                result = await fetch_page(url=target_url)
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": result}]},
                }

            elif name == "deep_research":
                await verify_agent_token(authorization, required_scope="search")
                q = args.get("query", "")
                await log_agent_activity(agent["name"], "deep_research", target=q)
                async with httpx.AsyncClient(timeout=120.0) as client:
                    try:
                        r = await client.post("http://gpt-researcher:8000/research", json={"query": q})
                        result = r.text
                    except Exception as e:
                        result = f"GPT Researcher error: {e}"
                return {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {"content": [{"type": "text", "text": result}]},
                }

            elif name == "stealth_scrape":
                await verify_agent_token(authorization, required_scope="scrape")
                target_url = args.get("url", "")
                await log_agent_activity(agent["name"], "stealth_scrape", target=target_url)
                async with httpx.AsyncClient(timeout=30.0) as client:
                    try:
                        r = await client.post("http://scrapling:8000/fetch", json={"url": target_url})
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
        authorization: str | None = Header(None),
    ):
        """REST search endpoint for direct agent querying."""
        agent = await verify_agent_token(authorization, required_scope="search")
        await log_agent_activity(agent["name"], "web_search", target=req.query, details={"limit": req.limit})
        result = await web_search(query=req.query, limit=req.limit)
        return {"query": req.query, "result": result}

    @app.post("/api/fetch")
    async def rest_fetch(
        req: FetchRequest,
        authorization: str | None = Header(None),
    ):
        """REST page fetch endpoint for direct agent scraping."""
        agent = await verify_agent_token(authorization, required_scope="scrape")
        await log_agent_activity(agent["name"], "fetch_page", target=req.url)
        result = await fetch_page(url=req.url)
        return {"url": req.url, "result": result}

    return app


app = create_gateway_app()
