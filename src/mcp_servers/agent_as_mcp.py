# agent_server.py  (renamed for clarity)
import asyncio
import logging
import sys
from mcp.server.fastmcp import FastMCP
 
# ─────────────────────────  Build the server  ──────────────────────────
mcp = FastMCP("agent")
from models.ModelConfig import ModelConfig
from models.BaseAgent import BaseAgent

@mcp.tool()
async def _run_sandbox_operation(code: str) -> str:
    """
    Create a Python 3.11 container. 
    execute *code* inside, and return the program’s stdout.
    """
    # Lazy imports so they execute *after* the MCP handshake


    model_cfg = ModelConfig()
    mcp_tools = ["filesystem", "docker"]

    agent = BaseAgent(
        model_name="gpt-4o-mini",
        model_config=model_cfg,
        remote_model=True,
        mcp_tools=mcp_tools,
    )

 
    prompt = f"""
    Create a Docker container with Python 3.11,
    then run the following code and return its output:

    {code}
    """

    response, _meta = await agent.run(prompt)
    return response


# @mcp.tool()
# async def run_sandbox_operation(code: str) -> str:
#     """
#     Run a sandbox operation that creates a docker container, mounts the local ./sandbox directory to /sandbox,
#     and executes the provided Python code inside the container.
    
#     Args:
#         code: The Python code to execute inside the sandbox container

#     Returns:
#         The output of the Python code execution
#     """
#     return await _run_sandbox_operation(code)


# ──────────────────────────  Bootstrap  ────────────────────────────────
if __name__ == "__main__":
    # run() is fine here because we’re in a *sync* context
    mcp.run(transport="stdio")        # handshake goes out first ✔

    # asyncio.run(_run_sandbox_operation("print('Hello, World!')"))
