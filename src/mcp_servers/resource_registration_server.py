import mcp
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from typing import List
from models.locations import DATA_GENERIC_DIR, SANDBOX_TASK_DIR
import os
import json
from src.mcp_descriptions.task_refinement import RESOURCE_REGISTRATION_DESCRIPTION

mcp = FastMCP("resource_registration")

class ResourceRegistrationInput(BaseModel):
    resource_name: str
    resource_type: str
    resource_description: str
    resource_location: str



@mcp.tool(name="output_resource_registration_report", description=RESOURCE_REGISTRATION_DESCRIPTION, tags=["resource_registration"])
def output_resource_registration_report(meta_task_name: str, resource_registration_input: List[ResourceRegistrationInput]) -> str:
    # output the resource registration report to a file
    # the file should be in the sandbox/tasks/meta_task_name/resource_registration_report.md
    file_path = os.path.join(SANDBOX_TASK_DIR, meta_task_name, "resources.json")
    
    # Ensure the directory exists
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    # Convert the list of ResourceRegistrationInput objects to a list of dictionaries
    resource_data = [resource.model_dump() for resource in resource_registration_input]
    
    # write the resource registration input to the file
    with open(file_path, "w") as f:
        f.write(json.dumps(resource_data, indent=4))
    return f"Resource registration report output to {file_path}"

if __name__ == "__main__":  
    mcp.run(transport="stdio")
    # make the test dir 
    # os.makedirs(os.path.join(SANDBOX_TASK_DIR, "test"), exist_ok=True)
    # output_resource_registration_report(meta_task_name="test", resource_registration_input=[ResourceRegistrationInput(resource_name="test", resource_type="test", resource_description="test", resource_location="test")])







