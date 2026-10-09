"""gateway: MCP gateway exposing curated search and scraping tools to AI agents."""

# Single source of the gateway version (ticket #4): FastAPI, initialize's
# serverInfo, and the package metadata all read this. Ticket #9 owns the
# release/tag policy; the contract version (X-Swarm-Contract, #5) is separate.
__version__ = "0.1.0"
