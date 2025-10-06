"""
Python sandbox operations functions
Functions for executing Python code in sandbox containers
"""
from __future__ import annotations
import asyncio
from models.SubBaseAgent import build_react_agent


# async def run_sandbox_python_code(code: str) -> str:
#     """
#     execute *code* inside a sandbox container, and return the program's stdout.
#     """
#     client, agent = await build_react_agent(mcp_keys=["generic", "sandbox"])

#     # 4️⃣  Prompt instructing the inner agent what to do
#     prompt = f"""
#     Create a Docker container with Python 3.11

#     then run the following code and return its output:

#     {code}
#     """

#     log.info("Inner ReAct agent starting")
    
#     result = await agent.ainvoke({"messages": prompt}, {"recursion_limit": 100})
#     reply = result["messages"][-1].content
#     log.info("Inner ReAct agent finished")
    
#     return reply

async def run_sandbox_operation_python_file(script_path: str) -> str:
    """
    Run a Python file in the sandbox.

    Args:
        script_path: The path to the python file to run. Please note that the file path is relative to the sandbox directory. e.g. "sandbox/code/<meta_task_name>/<task_index>/<script_name>.py"
    """
    prompt = f"""
    Use or create a Docker container with Python 3.11, mount the /sandbox directory to /sandbox. and mount the /data directory to /data. 

    Here are the dockers available and their details:


    Give it a random name. 

    then run the following python file and return its output:

    {script_path}

    Make sure you confirm the file path has the file, if not, search for the file in the /sandbox directory or /data directory and correct the file path.
    
    In your final response, please include the docker container id and the full "docker exec .." command you used to execute the script. 

    With the following format:
    ```
    docker_container_id: <docker_container_id>
    execution_command: <docker exec <docker_container_id> <execution_command>
    docker_output: <docker_output>
    status: <status of the execution, success or failed>
    ```
    """
    client, agent = await build_react_agent(mcp_keys=["generic", "sandbox"])
    result = await agent.ainvoke({"messages": prompt})
    reply = result["messages"][-1].content
    return reply 

if __name__ == "__main__":
    result = asyncio.run(run_sandbox_operation_python_file("sandbox/code/mcp_creation/0/mcp_creation.py"))
    print(result)