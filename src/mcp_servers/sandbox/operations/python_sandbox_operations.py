"""
Python sandbox operations functions
Functions for executing Python code in sandbox containers
"""
from __future__ import annotations
import asyncio
import logging
from models.SubBaseAgent import build_react_agent

log = logging.getLogger(__name__)

async def run_sandbox_python_code(code: str) -> str:
    """
    execute *code* inside a sandbox container, and return the program's stdout.
    """
    client, agent = await build_react_agent(mcp_keys=["generic_file_operations", "docker"])

    # 4️⃣  Prompt instructing the inner agent what to do
    prompt = f"""
    Create a Docker container with Python 3.11

    then run the following code and return its output:

    {code}
    """

    log.info("Inner ReAct agent starting")
    
    result = await agent.ainvoke({"messages": prompt}, {"recursion_limit": 100})
    reply = result["messages"][-1].content
    log.info("Inner ReAct agent finished")
    
    return reply

async def run_sandbox_operation_python_file(file_path: str) -> str:
    """
    Run a Python file in the sandbox.

    Args:
        file_path: The path to the python file to run. Please note that the file path is relative to the /sandbox directory.
    """
    prompt = f"""
    Create a Docker container with Python 3.11, mount the /sandbox directory to /sandbox. and mount the /data directory to /data. 

    Give it a random name. 

    then run the following python file and return its output:

    {file_path}

    Make sure you confirm the file path has the file, if not, search for the file in the /sandbox directory or /data directory and correct the file path.
    
    In your final response, please include the docker container id and the full "docker exec .." command you used to execute the script. 

    With the following format:
    ```
    docker_container_id: <docker_container_id>
    execution_command: <docker exec <docker_container_id> <execution_command>
    docker_output: <docker_output>
    ```
    """
    client, agent = await build_react_agent(mcp_keys=["generic_file_operations", "docker"])

    result = await agent.ainvoke({"messages": prompt})
    reply = result["messages"][-1].content
    return reply 