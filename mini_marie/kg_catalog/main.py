"""Catalog MCP: list mini_marie KG domains, endpoints, cache status."""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP

from mini_marie.kg_catalog import catalog

mcp = FastMCP(name="kg-catalog")


@mcp.tool(name="list_kg_domains", description="List all mini_marie KG domains and MCP servers")
def list_kg_domains() -> str:
    return catalog.list_kg_domains_text()


@mcp.tool(name="describe_kg_domain", description="Details for one domain id (mof, city, sg_kb, ...)")
def describe_kg_domain(domain_id: str) -> str:
    return catalog.describe_kg_domain_text(domain_id)


@mcp.tool(name="kg_cache_status", description="Report which local cache files exist and sizes")
def kg_cache_status() -> str:
    return catalog.kg_cache_status_text()


if __name__ == "__main__":
    mcp.run()
