from fastmcp import FastMCP
import subprocess
import json
import os
import shutil
import logging

from models.locations import DATA_GENERIC_DIR, SANDBOX_DIR  
from src.mcp_descriptions.docker import (
    DOCKER_REMOVE_CONTAINER_DESCRIPTION,
    DOCKER_LIST_RUNNING_CONTAINERS_DESCRIPTION,
    DOCKER_EXECUTE_PYTHON_SCRIPT_IN_CONTAINER_DESCRIPTION,
    DOCKER_CREATE_CONTAINER_DESCRIPTION,
    DOCKER_EXECUTE_COMMAND_IN_CONTAINER_DESCRIPTION,
    DOCKER_PYTHON_EXECUTION_IN_CONTAINER_DESCRIPTION
)

# -------------------- CONFIG --------------------
mcp = FastMCP("docker")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docker_mcp")

# -------------------- HELPERS --------------------

def correct_wsl_path(path: str) -> str:
    """
    Correct mnt/ to /mnt/
    """
    if path.startswith("/mnt/"):
        return path.replace("/mnt/", "/mnt/")
    else:
        return path

def docker_available():
    if shutil.which("docker") is None:
        return False, "Docker command not found. Please install Docker and ensure it's in PATH."
    return True, "Docker command found."

# -------------------- TOOLS --------------------

@mcp.tool(name="remove_container", description=DOCKER_REMOVE_CONTAINER_DESCRIPTION, tags=["docker"])
def remove_container(container_id: str) -> str:
    available, msg = docker_available()
    feedback = {"action": "remove_container", "container_id": container_id, "docker_available": available, "docker_check_msg": msg}
    if not available:
        feedback["result"] = "error"
        feedback["detail"] = msg
        return json.dumps(feedback)
    try:
        result = subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, text=True, check=True)
        feedback["result"] = "success"
        feedback["stdout"] = result.stdout
        feedback["stderr"] = result.stderr
        feedback["detail"] = f"Container {container_id} removed successfully"
        return json.dumps(feedback)
    except subprocess.CalledProcessError as e:
        feedback["result"] = "error"
        feedback["stdout"] = e.stdout
        feedback["stderr"] = e.stderr
        feedback["detail"] = f"Error removing container '{container_id}': {e.stderr}"
        return json.dumps(feedback)

@mcp.tool(name="list_running_containers", description=DOCKER_LIST_RUNNING_CONTAINERS_DESCRIPTION, tags=["docker"])
def list_running_containers() -> str:
    available, msg = docker_available()
    feedback = {"action": "list_running_containers", "docker_available": available, "docker_check_msg": msg}
    if not available:
        feedback["result"] = "error"
        feedback["detail"] = msg
        return json.dumps(feedback)
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "table {{.ID}}\t{{.Image}}\t{{.Command}}\t{{.CreatedAt}}\t{{.Status}}\t{{.Ports}}\t{{.Names}}"],
            capture_output=True,
            text=True,
            check=True
        )
        feedback["result"] = "success"
        feedback["stdout"] = result.stdout
        feedback["stderr"] = result.stderr
        feedback["detail"] = "Listed running containers successfully"
        return json.dumps(feedback)
    except subprocess.CalledProcessError as e:
        feedback["result"] = "error"
        feedback["stdout"] = e.stdout
        feedback["stderr"] = e.stderr
        feedback["detail"] = f"Error running 'docker ps': {e.stderr}"
        return json.dumps(feedback)

@mcp.tool(name="execute_python_script_in_container", description=DOCKER_EXECUTE_PYTHON_SCRIPT_IN_CONTAINER_DESCRIPTION, tags=["docker"])
def execute_python_script_in_container(container_id: str, script_path: str, args: list = None) -> str:
    available, msg = docker_available()
    feedback = {
        "action": "execute_python_script_in_container",
        "container_id": container_id,
        "script_path": script_path,
        "args": args,
        "docker_available": available,
        "docker_check_msg": msg
    }
    if not available:
        feedback["result"] = "error"
        feedback["detail"] = msg
        return json.dumps(feedback)
    try:
        cmd = ["docker", "exec", container_id, "python", script_path]
        if args:
            cmd.extend(args)
        logger.info(f"Executing script in container: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        feedback["result"] = "success"
        feedback["command"] = cmd
        feedback["stdout"] = result.stdout
        feedback["stderr"] = result.stderr
        feedback["detail"] = f"Script '{script_path}' executed in container '{container_id}'"
        return json.dumps(feedback)
    except subprocess.CalledProcessError as e:
        feedback["result"] = "error"
        feedback["command"] = cmd
        feedback["stdout"] = e.stdout
        feedback["stderr"] = e.stderr
        feedback["detail"] = f"Failed to run script '{script_path}' in container '{container_id}' with args {args}: {e.stderr}"
        return json.dumps(feedback)

def _create_container_command(image: str, name: str, detach: bool = True) -> str:
    available, msg = docker_available()
    feedback = {
        "action": "create_container",
        "image": image,
        "name": name,
        "detach": detach,
        "docker_available": available,
        "docker_check_msg": msg
    }
    if not available:
        feedback["result"] = "error"
        feedback["detail"] = msg
        return json.dumps(feedback)
    try:
        # Check if a container with the same name exists (running or stopped)
        check_cmd = ["docker", "ps", "-a", "-q", "-f", f"name=^{name}$"]
        existing = subprocess.run(check_cmd, capture_output=True, text=True, check=True)
        existing_id = existing.stdout.strip()
        feedback["existing_container_id"] = existing_id
        if existing_id:
            logger.info(f"Container with name '{name}' already exists (ID: {existing_id}). Removing it first.")
            remove_cmd = ["docker", "rm", "-f", name]
            remove_result = subprocess.run(remove_cmd, capture_output=True, text=True, check=True)
            feedback["remove_existing_stdout"] = remove_result.stdout
            feedback["remove_existing_stderr"] = remove_result.stderr

        cmd = ["docker", "run"]
        if detach:
            cmd.append("-d")
        cmd.extend(["--name", name])

        sandbox_path = correct_wsl_path(SANDBOX_DIR)
        data_path = correct_wsl_path(DATA_GENERIC_DIR)

        # cmd.extend(["-v", f"{sandbox_path}:/sandbox"])
        # cmd.extend(["-v", f"{data_path}:/data/generic_data"])

        # use hardcoded path for testing 
        cmd.extend(["-v", "/mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/sandbox:/sandbox"])
        cmd.extend(["-v", "/mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data:/data/generic_data"])

        cmd.append(image)
        cmd.extend(["tail", "-f", "/dev/null"])

        logger.info(f"Creating container: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        container_id = result.stdout.strip()
        feedback["run_command"] = cmd
        feedback["run_stdout"] = result.stdout
        feedback["run_stderr"] = result.stderr

        if not container_id:
            logger.warning("Container ID not returned, attempting fallback using name...")
            fallback = subprocess.run(["docker", "ps", "-qf", f"name={name}"], capture_output=True, text=True, check=True)
            container_id = fallback.stdout.strip()
            feedback["fallback_container_id"] = container_id
            feedback["fallback_stdout"] = fallback.stdout
            feedback["fallback_stderr"] = fallback.stderr

        if not container_id:
            feedback["result"] = "error"
            feedback["detail"] = f"Container '{name}' was created but ID could not be determined."
            return json.dumps(feedback)

        feedback["result"] = "success"
        feedback["container_id"] = container_id
        feedback["detail"] = f"Container '{name}' created successfully with ID: {container_id}"
        return json.dumps(feedback)
    except subprocess.CalledProcessError as e:
        feedback["result"] = "error"
        feedback["stdout"] = e.stdout
        feedback["stderr"] = e.stderr
        feedback["detail"] = f"Error creating container '{name}' with image '{image}': {e.stderr}"
        return json.dumps(feedback)

@mcp.tool(name="create_container", description=DOCKER_CREATE_CONTAINER_DESCRIPTION, tags=["docker"])
def create_container(image: str, name: str, detach: bool = True) -> str:
    return _create_container_command(image, name, detach)    

@mcp.tool(name="execute_command_in_container", description=DOCKER_EXECUTE_COMMAND_IN_CONTAINER_DESCRIPTION, tags=["docker"])
async def execute_command_in_container(container_id: str, command: str) -> str:
    available, msg = docker_available()
    feedback = {
        "action": "execute_command_in_container",
        "container_id": container_id,
        "command": command,
        "docker_available": available,
        "docker_check_msg": msg
    }
    if not available:
        feedback["result"] = "error"
        feedback["detail"] = msg
        return json.dumps(feedback)
    try:
        cmd_args = command.split()
        cmd = ["docker", "exec", container_id] + cmd_args
        logger.info(f"Executing command in container: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        feedback["result"] = "success"
        feedback["exec_command"] = cmd
        feedback["stdout"] = result.stdout
        feedback["stderr"] = result.stderr
        feedback["detail"] = f"Command executed: {' '.join(cmd)}"
        return json.dumps(feedback)
    except subprocess.CalledProcessError as e:
        feedback["result"] = "error"
        feedback["exec_command"] = cmd
        feedback["stdout"] = e.stdout
        feedback["stderr"] = e.stderr
        feedback["detail"] = f"Failed to execute command '{command}' in container '{container_id}': {e.stderr}"
        return json.dumps(feedback)

@mcp.tool(name="python_execution_in_container", description=DOCKER_PYTHON_EXECUTION_IN_CONTAINER_DESCRIPTION, tags=["docker"])
def python_execution_in_container(container_id: str, code: str) -> str:
    available, msg = docker_available()
    feedback = {
        "action": "python_execution_in_container",
        "container_id": container_id,
        "code": code,
        "docker_available": available,
        "docker_check_msg": msg
    }
    if not available:
        feedback["result"] = "error"
        feedback["detail"] = msg
        return json.dumps(feedback)
    try:
        logger.info(f"Executing Python code in container '{container_id}'")
        cmd = ["docker", "exec", container_id, "python", "-c", code]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        feedback["result"] = "success"
        feedback["exec_command"] = cmd
        feedback["stdout"] = result.stdout
        feedback["stderr"] = result.stderr
        feedback["detail"] = f"Python code executed in container '{container_id}'"
        return json.dumps(feedback)
    except subprocess.CalledProcessError as e:
        feedback["result"] = "error"
        feedback["exec_command"] = cmd
        feedback["stdout"] = e.stdout
        feedback["stderr"] = e.stderr
        feedback["detail"] = f"Failed to execute Python code in container '{container_id}': {e.stderr}"
        return json.dumps(feedback)

# -------------------- MAIN ENTRYPOINT --------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
    # test_path = "/mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data/jinfeng/extracted_data.csv"
    # print(wsl_to_windows_path(test_path))

   #  _create_container_command("python:3.11", "test_container", detach=True)
