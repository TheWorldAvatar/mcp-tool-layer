from fastmcp import FastMCP
from src.mcp_descriptions.docker import (
    DOCKER_REMOVE_CONTAINER_DESCRIPTION,
    DOCKER_LIST_RUNNING_CONTAINERS_DESCRIPTION,
    DOCKER_EXECUTE_PYTHON_SCRIPT_IN_CONTAINER_DESCRIPTION,
    DOCKER_CREATE_CONTAINER_DESCRIPTION,
    DOCKER_EXECUTE_COMMAND_IN_CONTAINER_DESCRIPTION,
    DOCKER_PYTHON_EXECUTION_IN_CONTAINER_DESCRIPTION
)
from typing import Literal  
# Import functions from separated files
from src.mcp_servers.docker.operations.docker_operations import (
    remove_container,
    list_running_containers,
    execute_python_script_in_container,
    create_container,
    execute_command_in_container,
    python_execution_in_container,
    register_docker_container,
    list_registered_docker_containers_for_task
)
from src.utils.global_logger import get_logger, mcp_tool_logger

# -------------------- CONFIG --------------------
mcp = FastMCP("docker")
logger = get_logger("mcp_server", "docker_main")

# -------------------- DOCKER TOOLS --------------------

@mcp.tool(name="register_docker_container", description="Register a docker container to the database for future reuse. Description should include the python version used in the container, and the pip packages installed in the container. The mounted volumes should be included in the description. meta_task_name is the task name, as docker containers are specific to a task.", tags=["docker"])
@mcp_tool_logger
def register_docker_container_tool(container_id: str, container_name: str, description: str, status: Literal["running", "stopped", "created"], meta_task_name: str    ) -> str:
    return register_docker_container(container_id, container_name, description, status, meta_task_name)

@mcp.tool(name="list_registered_docker_containers_for_task", description="List all registered docker containers in the database for a specific task.", tags=["docker"])
@mcp_tool_logger
def list_registered_docker_containers_for_task_tool(meta_task_name: str) -> str:
    return list_registered_docker_containers_for_task(meta_task_name)
 
@mcp.tool(name="remove_container", description=DOCKER_REMOVE_CONTAINER_DESCRIPTION, tags=["docker"])
@mcp_tool_logger
def remove_container_tool(container_id: str) -> str:
    return remove_container(container_id)

@mcp.tool(name="list_running_containers", description=DOCKER_LIST_RUNNING_CONTAINERS_DESCRIPTION, tags=["docker"])
@mcp_tool_logger
def list_running_containers_tool() -> str:
    return list_running_containers()

@mcp.tool(name="execute_python_script_in_container", description=DOCKER_EXECUTE_PYTHON_SCRIPT_IN_CONTAINER_DESCRIPTION, tags=["docker"])
@mcp_tool_logger
def execute_python_script_in_container_tool(container_id: str, script_path: str, args: list = None) -> str:
    return execute_python_script_in_container(container_id, script_path, args)

@mcp.tool(name="create_container", description=DOCKER_CREATE_CONTAINER_DESCRIPTION, tags=["docker"])
@mcp_tool_logger
def create_container_tool(image: str, name: str, detach: bool = True) -> str:
    return create_container(image, name, detach)

@mcp.tool(name="execute_command_in_container", description=DOCKER_EXECUTE_COMMAND_IN_CONTAINER_DESCRIPTION, tags=["docker"])
@mcp_tool_logger
async def execute_command_in_container_tool(container_id: str, command: str) -> str:
    return await execute_command_in_container(container_id, command)

@mcp.tool(name="python_execution_in_container", description=DOCKER_PYTHON_EXECUTION_IN_CONTAINER_DESCRIPTION, tags=["docker"])
@mcp_tool_logger
def python_execution_in_container_tool(container_id: str, code: str) -> str:
    return python_execution_in_container(container_id, code)

# -------------------- MAIN ENTRYPOINT --------------------
if __name__ == "__main__":
    mcp.run(transport="stdio") 