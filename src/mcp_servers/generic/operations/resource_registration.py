import mcp
from pydantic import BaseModel
from typing import List
from models.locations import DATA_GENERIC_DIR, SANDBOX_TASK_DIR
import os
import json
from typing import Optional


class ResourceRegistrationInput(BaseModel):
    resource_name: str
    resource_type: str
    resource_description: str
    resource_location: str
    # optional attribtus for script only 
    # 1. the docker container name for execution 
    # 2. the command to execute the script via docker 
    # 3. extra libraries installed for that 
    docker_container_id: Optional[str] = None
    execution_command: Optional[str] = None
    extra_libraries: Optional[List[str]] = None

def convert_to_absolute_path(path: str) -> str:
    # Handle paths starting with /data/generic_data/
    if path.startswith("/data/generic_data/"):
        return os.path.join(DATA_GENERIC_DIR, path.split("/data/generic_data/")[1])
    
    # Handle paths starting with /sandbox/
    elif path.startswith("/sandbox/"):
        return os.path.join(SANDBOX_TASK_DIR, path.split("/sandbox/")[1])
    
    # Handle paths starting with data/generic_data/ (without leading slash)
    elif path.startswith("data/generic_data/"):
        return os.path.join(DATA_GENERIC_DIR, path.split("data/generic_data/")[1])
    
    # Handle paths starting with sandbox/ (without leading slash)
    elif path.startswith("sandbox/"):
        return os.path.join(SANDBOX_TASK_DIR, path.split("sandbox/")[1])
    
    # Return path as-is if no patterns match
    else:
        return path


def output_resource_registration_report(meta_task_name: str, resource_registration_input: List[ResourceRegistrationInput]) -> str:
    # output the resource registration report to a file
    # the file should be in the sandbox/tasks/meta_task_name/resource_registration_report.md

    file_path = os.path.join(SANDBOX_TASK_DIR, meta_task_name, "resources.json")
    
    # Ensure the directory exists
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    # resource_location: str, make sure you convert it to the absolute path. 
    for resource in resource_registration_input:
        resource.resource_location = convert_to_absolute_path(resource.resource_location)

    # Convert the list of ResourceRegistrationInput objects to a list of dictionaries
    new_resource_data = [resource.model_dump() for resource in resource_registration_input]
    
    # Load existing resources if file exists, otherwise start with empty list
    existing_resources = []
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                existing_resources = json.loads(f.read())
        except (json.JSONDecodeError, FileNotFoundError):
            existing_resources = []
    
    # Append new resources to existing ones
    all_resources = existing_resources + new_resource_data
    
    # write the resource registration input to the file
    with open(file_path, "w") as f:
        f.write(json.dumps(all_resources, indent=4))
    return f"Resource registration report output to {file_path}" 