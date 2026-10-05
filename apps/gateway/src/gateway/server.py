"""MCP Gateway server implementing Streamable HTTP transport and scoped tools."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
import aiosqlite
import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from mcp.server.mcpserver import MCPServer

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080")
CRAWL4AI_URL = os.environ.get("CRAWL4AI_URL", "http://crawl4ai:11235")
DB_PATH = os.environ.get("SWARM_DB_PATH", "/var/lib/scraper-swarm/panel.db")

mcp_server = MCPServer(name="ScraperSwarmGateway")


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
        """Authenticated MCP Streamable HTTP endpoint."""
        await verify_agent_token(authorization, required_scope="search")
        # In full production this streams JSON-RPC tool calls
        return {"status": "connected", "tools": ["web_search", "fetch_page"]}

    return app


app = create_gateway_app()
