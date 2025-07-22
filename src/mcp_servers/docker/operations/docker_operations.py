import subprocess
import json
import os
import shutil
from typing import Literal
from models.locations import DATA_GENERIC_DIR, SANDBOX_DIR
from src.utils.docker_db_operations import DockerDBOperator, DockerResource

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

# -------------------- FUNCTIONS --------------------

def remove_container(container_id: str) -> str:
    available, msg = docker_available()
    if not available:
        return json.dumps({"result": "error", "detail": msg})
    try:
        result = subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, text=True, check=True)
        return json.dumps({
            "result": "success",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "detail": f"Container {container_id} removed successfully"
        })
    except subprocess.CalledProcessError as e:
        return json.dumps({
            "result": "error",
            "stdout": e.stdout,
            "stderr": e.stderr,
            "detail": f"Error removing container '{container_id}': {e.stderr}"
        })

def list_running_containers() -> str:
    available, msg = docker_available()
    if not available:
        return json.dumps({"result": "error", "detail": msg})
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "table {{.ID}}\t{{.Image}}\t{{.Command}}\t{{.CreatedAt}}\t{{.Status}}\t{{.Ports}}\t{{.Names}}"],
            capture_output=True,
            text=True,
            check=True
        )
        return json.dumps({
            "result": "success",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "detail": "Listed running containers successfully"
        })
    except subprocess.CalledProcessError as e:
        return json.dumps({
            "result": "error",
            "stdout": e.stdout,
            "stderr": e.stderr,
            "detail": f"Error running 'docker ps': {e.stderr}"
        })

def execute_python_script_in_container(container_id: str, script_path: str, args: list = None) -> str:
    available, msg = docker_available()
    if not available:
        return json.dumps({"result": "error", "detail": msg})
    try:
        cmd = ["docker", "exec", container_id, "python", script_path]
        if args:
            cmd.extend(args)
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.dumps({
            "result": "success",
            "command": cmd,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "detail": f"Script '{script_path}' executed in container '{container_id}'"
        })
    except subprocess.CalledProcessError as e:
        return json.dumps({
            "result": "error",
            "command": cmd,
            "stdout": e.stdout,
            "stderr": e.stderr,
            "detail": f"Failed to run script '{script_path}' in container '{container_id}' with args {args}: {e.stderr}"
        })

def _create_container_command(image: str, name: str, detach: bool = True) -> str:
    available, msg = docker_available()
    if not available:
        return json.dumps({"result": "error", "detail": msg})
    try:
        # Check if a container with the same name exists (running or stopped)
        check_cmd = ["docker", "ps", "-a", "-q", "-f", f"name=^{name}$"]
        existing = subprocess.run(check_cmd, capture_output=True, text=True, check=True)
        existing_id = existing.stdout.strip()
        if existing_id:
            remove_cmd = ["docker", "rm", "-f", name]
            subprocess.run(remove_cmd, capture_output=True, text=True, check=True)

        cmd = ["docker", "run"]
        if detach:
            cmd.append("-d")
        cmd.extend(["--name", name])

        sandbox_path = correct_wsl_path(SANDBOX_DIR)
        data_path = correct_wsl_path(DATA_GENERIC_DIR)
        # use hardcoded path for testing 
        cmd.extend(["-v", "/mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/sandbox:/sandbox"])
        cmd.extend(["-v", "/mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data:/data/generic_data"])

        cmd.append(image)
        cmd.extend(["tail", "-f", "/dev/null"])

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        container_id = result.stdout.strip()

        if not container_id:
            fallback = subprocess.run(["docker", "ps", "-qf", f"name={name}"], capture_output=True, text=True, check=True)
            container_id = fallback.stdout.strip()

        if not container_id:
            return json.dumps({
                "result": "error",
                "detail": f"Container '{name}' was created but ID could not be determined."
            })

        return json.dumps({
            "result": "success",
            "container_id": container_id,
            "detail": f"Container '{name}' created successfully with ID: {container_id}"
        })
    except subprocess.CalledProcessError as e:
        return json.dumps({
            "result": "error",
            "stdout": e.stdout,
            "stderr": e.stderr,
            "detail": f"Error creating container '{name}' with image '{image}': {e.stderr}"
        })

def create_container(image: str, name: str, detach: bool = True) -> str:
    return _create_container_command(image, name, detach)    

async def execute_command_in_container(container_id: str, command: str) -> str:

    import re

    # Check if the command is a python execution that should use execute_python_script_in_container
    python_cmd_pattern = re.compile(
        r"""^
            python(?:\d+(?:\.\d+)*)?      # python, python3, python3.11, etc.
            \s+
            (
                (?:[^\s]+\.py)            # script.py
                |
                -c\s+.+                   # -c "code"
                |
                -m\s+[^\s]+               # -m module
            )
        """,
        re.IGNORECASE | re.VERBOSE
    )
    if python_cmd_pattern.match(command.strip()):
        return json.dumps({
            "result": "error",
            "detail": (
                "Direct python execution commands (e.g., 'python script.py', 'python -c', 'python -m') "
                "are not allowed here. Please use the 'execute_python_script_in_container' tool for running Python code or scripts."
            )
        })

    
 
    available, msg = docker_available()
    if not available:
        return json.dumps({"result": "error", "detail": msg})
    try:
        cmd_args = command.split()
        cmd = ["docker", "exec", container_id] + cmd_args
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.dumps({
            "result": "command executed",
            "exec_command": cmd,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "detail": f"Command executed: {' '.join(cmd)}"
        })
    except subprocess.CalledProcessError as e:
        return json.dumps({
            "result": "error",
            "exec_command": cmd,
            "stdout": e.stdout,
            "stderr": e.stderr,
            "detail": f"Failed to execute command '{command}' in container '{container_id}': {e.stderr}"
        })

def python_execution_in_container(container_id: str, code: str) -> str:
    available, msg = docker_available()
    if not available:
        return json.dumps({"result": "error", "detail": msg})
    try:
        cmd = ["docker", "exec", container_id, "python", "-c", code]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        return json.dumps({
            "result": "success",
            "exec_command": cmd,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "detail": f"Python code executed in container '{container_id}'"
        })
    except subprocess.CalledProcessError as e:
        return json.dumps({
            "result": "error",
            "exec_command": cmd,
            "stdout": e.stdout,
            "stderr": e.stderr,
            "detail": f"Failed to execute Python code in container '{container_id}': {e.stderr}"
        }) 

def register_docker_container(container_id: str, container_name: str, description: str, status: Literal["running", "stopped", "created"], meta_task_name: str) -> str:
    docker_db_operator = DockerDBOperator()
    docker_db_operator.register_docker_resource(DockerResource(container_id=container_id, container_name=container_name, description=description, status=status, meta_task_name=meta_task_name))
    return json.dumps({
        "result": "success",
        "container_id": container_id,
        "container_name": container_name,
        "description": description,
        "meta_task_name": meta_task_name,
        "detail": f"Docker container '{container_name}' registered successfully"
    })


def list_registered_docker_containers_for_task(meta_task_name: str) -> str:
    docker_db_operator = DockerDBOperator()
    docker_resources = docker_db_operator.get_all_docker_resources(meta_task_name)
    return "\n".join([str(docker_resource) for docker_resource in docker_resources])


if __name__ == "__main__":
    import asyncio
    rst = asyncio.run(execute_command_in_container("32226bb199176361b9c64e415c4d4de7d530e96b6ecc478ca7f3d665353705c0", "pip install cclib"))
    import pprint
    pprint.pprint(rst)
