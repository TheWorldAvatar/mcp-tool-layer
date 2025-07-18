from fastmcp import FastMCP
from src.mcp_descriptions.docker import (
    DOCKER_REMOVE_CONTAINER_DESCRIPTION,
    DOCKER_LIST_RUNNING_CONTAINERS_DESCRIPTION,
    DOCKER_EXECUTE_PYTHON_SCRIPT_IN_CONTAINER_DESCRIPTION,
    DOCKER_CREATE_CONTAINER_DESCRIPTION,
    DOCKER_EXECUTE_COMMAND_IN_CONTAINER_DESCRIPTION,
    DOCKER_PYTHON_EXECUTION_IN_CONTAINER_DESCRIPTION
)

# Import functions from separated files
from src.mcp_servers.docker.operations.docker_operations import (
    remove_container,
    list_running_containers,
    execute_python_script_in_container,
    create_container,
    execute_command_in_container,
    python_execution_in_container
)
from src.mcp_servers.sandbox.operations.python_sandbox_operations import run_sandbox_operation_python_file
from src.utils.global_logger import get_logger, mcp_tool_logger

# -------------------- CONFIG --------------------
mcp = FastMCP("sandbox")
logger = get_logger("mcp_server", "sandbox_main")

 
@mcp.tool()
@mcp_tool_logger
async def run_sandbox_operation_python_file_tool(file_path: str) -> str:
    """
    Run a Python file in the sandbox.

    Args:
        file_path: The path to the python file to run. Please note that the file path is relative to the sandbox directory. e.g. "sandbox/code/<meta_task_name>/<task_index>/<script_name>.py"
    """
    return await run_sandbox_operation_python_file(file_path)

# -------------------- MAIN ENTRYPOINT --------------------
if __name__ == "__main__":
    mcp.run(transport="stdio") 