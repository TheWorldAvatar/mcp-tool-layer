"""
agent_server.py
FastMCP server that offers a single tool: run_sandbox_operation(code:str) -> str
The tool itself spins up two MCP helpers ("filesystem" and "docker") and lets
a lightweight ReAct agent use them to execute code in a sandbox container.
"""
from __future__ import annotations
import asyncio
import logging
from fastmcp import FastMCP
from models.SubBaseAgent import build_react_agent
mcp = FastMCP("SandboxOuter")
 


@mcp.tool()
async def run_sandbox_python_code(code: str) -> str:
    """
    execute *code* inside a sandbox container, and return the program's stdout.
    """
    client, agent = await build_react_agent(mcp_keys=["filesystem", "docker"])

    # 4️⃣  Prompt instructing the inner agent what to do
    prompt = f"""
    Create a Docker container with Python 3.11

    then run the following code and return its output:

    {code}
    """

    log.info("Inner ReAct agent starting")
    
    result = await agent.ainvoke({"messages": prompt})
    reply = result["messages"][-1].content
    log.info("Inner ReAct agent finished")
    
    return reply

@mcp.tool()
async def run_sandbox_operation_python_file(file_path: str) -> str:
    """
    Run a Python file in the sandbox.

    Args:
        file_path: The path to the python file to run. Please note that the file path is relative to the /sandbox directory.
    """
    prompt = f"""
    Create a Docker container with Python 3.11, mount the /sandbox directory to /sandbox. and mount the /data directory to /data. 

    then run the following python file and return its output:

    {file_path}

    Make sure you confirm the file path has the file, if not, search for the file in the /sandbox directory or /data directory and correct the file path.
    """
    client, agent = await build_react_agent(mcp_keys=["filesystem", "docker"])

    result = await agent.ainvoke({"messages": prompt})
    reply = result["messages"][-1].content
    return reply


if __name__ == "__main__":
    mcp.run(transport="stdio")
    # asyncio.run(run_sandbox_python_code("print('Hello, World!')"))