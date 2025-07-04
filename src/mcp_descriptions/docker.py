# Docker MCP Tool Descriptions

DOCKER_REMOVE_CONTAINER_DESCRIPTION = """Remove a Docker container

Args:
    container_id: The ID of the Docker container

Example:
    remove_container("1234567890")

Note: 
"""

DOCKER_LIST_RUNNING_CONTAINERS_DESCRIPTION = """List all running Docker containers using docker ps command"""

DOCKER_EXECUTE_PYTHON_SCRIPT_IN_CONTAINER_DESCRIPTION = """Execute a Python script in a Docker container, which is mounted to /sandbox

Args:
    container_id: The ID of the Docker container
    script_path: The path to the Python script in the container
    args: A list of arguments to pass to the Python script

Example:
    execute_python_script_in_container("1234567890", "/sandbox/hello_world.py")

Dependencies: 
    - The involved third party python libraries should be installed in the container before. 
"""

DOCKER_CREATE_CONTAINER_DESCRIPTION = """Create a new Docker container

Args:
    image: Docker image name and tag (e.g., 'python:3.11')
    name: Container name
    ports: Dictionary mapping host ports to container ports (e.g., {'8080': '80'})
    detach: Whether to run container in detached mode
"""

DOCKER_EXECUTE_COMMAND_IN_CONTAINER_DESCRIPTION = """Execute a command in a Docker container, this is a general command execution tool, which can be used to install python libraries, etc.

Args:
    container_id: The ID of the Docker container
    command: The command to execute in the container

Example:
    execute_command_in_container("1234567890", "pip install <library_name>")
"""

DOCKER_PYTHON_EXECUTION_IN_CONTAINER_DESCRIPTION = """Execute Python code in a Docker container, which is a string of code

Args:
    container_id: The ID of the Docker container
    code: The Python code to execute in the container

Dependencies: 
    - The involved third party python libraries should be installed in the container before. 
"""
