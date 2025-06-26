
from mcp.server.fastmcp import FastMCP
import subprocess
import json
import os
import logging

mcp = FastMCP("docker")

logger = logging.getLogger(__name__)
logger.addHandler(logging.FileHandler("docker.log"))    


@mcp.tool()
def remove_container(container_id: str) -> str:
    """Remove a Docker container
    
    Args:
        container_id: The ID of the Docker container

    Example:
        remove_container("1234567890")

    Note: 

    """
    try:
        subprocess.run(["docker", "rm", "-f", container_id], check=True)
        return f"Container {container_id} removed successfully"
    except subprocess.CalledProcessError as e:
        logger.error(f"Error removing container: {e.stderr}")
        return f"Error removing container: {e.stderr}"
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return f"Unexpected error: {str(e)}"

@mcp.tool()
def list_running_containers() -> str:
    """List all running Docker containers using docker ps command"""
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


@mcp.tool()
def execute_python_script_in_container(container_id: str, script_path: str, args: list = None) -> str:
    """Execute a Python script in a Docker container, which is mounted to /sandbox
    
    Args:
        container_id: The ID of the Docker container
        script_path: The path to the Python script in the container
        args: A list of arguments to pass to the Python script

    Example:
        execute_python_script_in_container("1234567890", "/sandbox/hello_world.py")

    Dependencies: 

        - The involved third party python libraries should be installed in the container before. 
    """
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


@mcp.tool()
def create_container(image: str, name: str, ports: dict = None, detach: bool = True) -> str:
    """Create a new Docker container
    
    Args:
        image: Docker image name and tag (e.g., 'python:3.11')
        name: Container name
        ports: Dictionary mapping host ports to container ports (e.g., {'8080': '80'})
        detach: Whether to run container in detached mode

    """
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
        
        # Add volume mount for sandbox/code directory
        sandbox_code_path = os.path.join(os.getcwd(), "sandbox")
        cmd.extend(["-v", f"{sandbox_code_path}:/sandbox"])
        
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

@mcp.tool()
async def execute_command_in_container(container_id: str, command: str) -> str:
    """Execute a command in a Docker container, this is a general command execution tool, which can be used to install python libraries, etc.
    
    Args:
        container_id: The ID of the Docker container
        command: The command to execute in the container

    Example:
        execute_command_in_container("1234567890", "pip install <library_name>")
    """
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
        return result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"Error executing generalcommand: {e.stderr}")
        return f"Error executing general command: {e.stderr}"
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return f"Unexpected error: {str(e)}"

     

@mcp.tool()
def python_execution_in_container(container_id: str, code: str) -> str:
    """Execute Python code in a Docker container, which is a string of code
    
    Args:
        container_id: The ID of the Docker container
        code: The Python code to execute in the container

    Dependencies: 
        - The involved third party python libraries should be installed in the container before. 
    """
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