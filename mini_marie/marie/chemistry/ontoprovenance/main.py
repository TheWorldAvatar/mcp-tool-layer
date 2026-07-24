"""OntoProvenance chemistry MCP."""

from mini_marie.marie.chemistry.mcp_factory import create_chemistry_mcp

mcp = create_chemistry_mcp("ontoprovenance")

if __name__ == "__main__":
    mcp.run()
