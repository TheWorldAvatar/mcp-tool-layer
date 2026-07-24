"""OntoMOPs chemistry MCP (T-Box; instance data on MOPs stack)."""

from mini_marie.marie.chemistry.mcp_factory import create_chemistry_mcp

mcp = create_chemistry_mcp("ontomops")

if __name__ == "__main__":
    mcp.run()
