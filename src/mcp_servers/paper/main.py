from fastmcp import FastMCP
from src.mcp_servers.paper.operations.output import output_paper, Paper
from typing import List

mcp = FastMCP(name="paper_output", instructions="""This is a tool to output the paper search results. It is used to output the paper search results to a file.
""")

@mcp.tool(name="output_paper", description="Output the paper search results to a file.", tags=["paper_output"])
def output_paper_tool(iteration: int, paper: List[Paper]) -> str:
    return output_paper(iteration, paper)

if __name__ == "__main__":
    mcp.run(transport="stdio")



