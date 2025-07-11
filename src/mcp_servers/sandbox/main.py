from fastmcp import FastMCP
import logging
from src.mcp_descriptions.docker import (
    DOCKER_REMOVE_CONTAINER_DESCRIPTION,
    DOCKER_LIST_RUNNING_CONTAINERS_DESCRIPTION,
    DOCKER_EXECUTE_PYTHON_SCRIPT_IN_CONTAINER_DESCRIPTION,
    DOCKER_CREATE_CONTAINER_DESCRIPTION,
    DOCKER_EXECUTE_COMMAND_IN_CONTAINER_DESCRIPTION,
    DOCKER_PYTHON_EXECUTION_IN_CONTAINER_DESCRIPTION
)

# Import functions from separated files
from src.mcp_servers.sandbox.operations.docker_operations import (
    remove_container,
    list_running_containers,
    execute_python_script_in_container,
    create_container,
    execute_command_in_container,
    python_execution_in_container
)

from src.mcp_servers.sandbox.operations.python_sandbox_operations import (
    run_sandbox_python_code,
    run_sandbox_operation_python_file
)

# -------------------- CONFIG --------------------
mcp = FastMCP("sandbox")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sandbox_mcp")

# -------------------- DOCKER TOOLS --------------------

@mcp.tool(name="remove_container", description=DOCKER_REMOVE_CONTAINER_DESCRIPTION, tags=["docker"])
def remove_container_tool(container_id: str) -> str:
    return remove_container(container_id)

@mcp.tool(name="list_running_containers", description=DOCKER_LIST_RUNNING_CONTAINERS_DESCRIPTION, tags=["docker"])
def list_running_containers_tool() -> str:
    return list_running_containers()

@mcp.tool(name="execute_python_script_in_container", description=DOCKER_EXECUTE_PYTHON_SCRIPT_IN_CONTAINER_DESCRIPTION, tags=["docker"])
def execute_python_script_in_container_tool(container_id: str, script_path: str, args: list = None) -> str:
    return execute_python_script_in_container(container_id, script_path, args)

@mcp.tool(name="create_container", description=DOCKER_CREATE_CONTAINER_DESCRIPTION, tags=["docker"])
def create_container_tool(image: str, name: str, detach: bool = True) -> str:
    return create_container(image, name, detach)

@mcp.tool(name="execute_command_in_container", description=DOCKER_EXECUTE_COMMAND_IN_CONTAINER_DESCRIPTION, tags=["docker"])
async def execute_command_in_container_tool(container_id: str, command: str) -> str:
    return await execute_command_in_container(container_id, command)

@mcp.tool(name="python_execution_in_container", description=DOCKER_PYTHON_EXECUTION_IN_CONTAINER_DESCRIPTION, tags=["docker"])
def python_execution_in_container_tool(container_id: str, code: str) -> str:
    return python_execution_in_container(container_id, code)

# -------------------- PYTHON SANDBOX TOOLS --------------------

@mcp.tool()
async def run_sandbox_python_code_tool(code: str) -> str:
    """
    execute *code* inside a sandbox container, and return the program's stdout.
    """
    return await run_sandbox_python_code(code)

@mcp.tool()
async def run_sandbox_operation_python_file_tool(file_path: str) -> str:
    """
    Run a Python file in the sandbox.

    Args:
        file_path: The path to the python file to run. Please note that the file path is relative to the /sandbox directory.
    """
    return await run_sandbox_operation_python_file(file_path)

# -------------------- MAIN ENTRYPOINT --------------------
if __name__ == "__main__":
    mcp.run(transport="stdio") 