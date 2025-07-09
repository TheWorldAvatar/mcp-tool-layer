
from fastmcp import FastMCP
import subprocess
import json
import os
import logging

from models.locations import DATA_GENERIC_DIR, SANDBOX_TASK_DIR

from src.mcp_descriptions.docker import (
    DOCKER_REMOVE_CONTAINER_DESCRIPTION,
    DOCKER_LIST_RUNNING_CONTAINERS_DESCRIPTION,
    DOCKER_EXECUTE_PYTHON_SCRIPT_IN_CONTAINER_DESCRIPTION,
    DOCKER_CREATE_CONTAINER_DESCRIPTION,
    DOCKER_EXECUTE_COMMAND_IN_CONTAINER_DESCRIPTION,
    DOCKER_PYTHON_EXECUTION_IN_CONTAINER_DESCRIPTION
)

mcp = FastMCP("docker")

logger = logging.getLogger(__name__)
logger.addHandler(logging.FileHandler("docker.log"))    


@mcp.tool(name="remove_container", description=DOCKER_REMOVE_CONTAINER_DESCRIPTION, tags=["docker"])
def remove_container(container_id: str) -> str:
    try:
        subprocess.run(["docker", "rm", "-f", container_id], check=True)
        return f"Container {container_id} removed successfully"
    except subprocess.CalledProcessError as e:
        logger.error(f"Error removing container: {e.stderr}")
        return f"Error removing container: {e.stderr}"
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return f"Unexpected error: {str(e)}"

@mcp.tool(name="list_running_containers", description=DOCKER_LIST_RUNNING_CONTAINERS_DESCRIPTION, tags=["docker"])
def list_running_containers() -> str:
    try:
        # Run docker ps command to get running containers
        result = subprocess.run(
            ["docker", "ps", "--format", "table {{.ID}}\t{{.Image}}\t{{.Command}}\t{{.CreatedAt}}\t{{.Status}}\t{{.Ports}}\t{{.Names}}"],
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Docker ps command result: {result.stdout}")
        return result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Error running docker ps: {e.stderr}")
        return f"Error running docker ps: {e.stderr}"
    except FileNotFoundError:
        logger.error("Docker command not found. Please ensure Docker is installed and accessible.")
        return "Docker command not found. Please ensure Docker is installed and accessible."


@mcp.tool(name="execute_python_script_in_container", description=DOCKER_EXECUTE_PYTHON_SCRIPT_IN_CONTAINER_DESCRIPTION, tags=["docker"])
def execute_python_script_in_container(container_id: str, script_path: str, args: list = None) -> str:
    try:
        # Build docker exec command
        cmd = ["docker", "exec", container_id, "python", script_path]
        
        # Add arguments if provided
        if args:
            cmd.extend(args)
        
        logger.info(f"Running docker exec command: {cmd}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Docker exec command result: {result.stdout}")
        return result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Error executing Python script: {e.stderr}")
        return f"Error executing Python script: {e.stderr}"
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return f"Unexpected error: {str(e)}"


@mcp.tool(name="create_container", description=DOCKER_CREATE_CONTAINER_DESCRIPTION, tags=["docker"])
def create_container(image: str, name: str, ports: dict = None, detach: bool = True) -> str:
    try:
        # Build docker run command
        cmd = ["docker", "run"]
        
        # Add detach flag if specified
        if detach:
            cmd.append("-d")
        
        # Add container name
        cmd.extend(["--name", name])
        
        # Add port mappings if provided
        if ports:
            for host_port, container_port in ports.items():
                cmd.extend(["-p", f"{host_port}:{container_port}"])
        
        # Add volume mounts for sandbox/code and data/generic_data directories
        sandbox_code_path = os.path.join(os.getcwd(), "sandbox")
        data_generic_path = os.path.join(os.getcwd(), "data", "generic_data")
        cmd.extend(["-v", f"{sandbox_code_path}:/sandbox"])
        cmd.extend(["-v", f"{data_generic_path}:/data/generic_data"])
        
        # Add the image
        cmd.append(image)
        
        # Add command to keep container running (tail -f /dev/null)
        cmd.extend(["tail", "-f", "/dev/null"])

        logger.info(f"Running docker run command: {cmd}")
        # Run docker run command to create a new container
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Docker run command result: {result.stdout}")
        container_id = result.stdout.strip()
        return f"Container '{name}' created successfully with ID: {container_id}"
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Error creating container: {e.stderr}")
        return f"Error creating container: {e.stderr}"
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return f"Unexpected error: {str(e)}"

@mcp.tool(name="execute_command_in_container", description=DOCKER_EXECUTE_COMMAND_IN_CONTAINER_DESCRIPTION, tags=["docker"])
async def execute_command_in_container(container_id: str, command: str) -> str:
    try:
        # Run command in the container
        logger.info(f"Running command: {command} in container: {container_id}")

        # split the command into a list of arguments
        cmd_args = command.split()

        cmd = ["docker", "exec", container_id] + cmd_args
        logger.info(f"Running docker exec general command: {cmd}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Command execution result: {result.stdout}")
        return f"Command executed: {' '.join(cmd)}\nOutput: {result.stdout}"
    except subprocess.CalledProcessError as e:
        logger.error(f"Error executing generalcommand: {e.stderr}")
        return f"Error executing general command: {e.stderr}"
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return f"Unexpected error: {str(e)}"

     

@mcp.tool(name="python_execution_in_container", description=DOCKER_PYTHON_EXECUTION_IN_CONTAINER_DESCRIPTION, tags=["docker"])
def python_execution_in_container(container_id: str, code: str) -> str:
    try:
        # Run python command in the container
        result = subprocess.run(
            ["docker", "exec", container_id, "python", "-c", code],
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Python execution result: {result.stdout}")
        return result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Error executing Python code: {e.stderr}")
        return f"Error executing Python code: {e.stderr}"
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return f"Unexpected error: {str(e)}"

# ---------- bootstrap the server --------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
    # create_container("python:3.11", "python3.11-tom-jerry")