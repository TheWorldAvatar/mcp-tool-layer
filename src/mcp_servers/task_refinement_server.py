
"""
This server is used to generate task files for the task decomposition agent. 

To make sure the Task Decomposition Agent generates syntactically correct task files, this server is used to generate task files. 
"""
from fastmcp import FastMCP
import subprocess
import json
import os
import logging
from pydantic import BaseModel
from typing import List
import uuid
import shutil
from models.locations import SANDBOX_TASK_DIR, SANDBOX_TASK_ARCHIVE_DIR, DATA_LOG_DIR
from datetime import datetime
from src.mcp_descriptions.task_refinement import TASK_REFINEMENT_DESCRIPTION, TASK_GROUP_REFINEMENT_DESCRIPTION

mcp = FastMCP("task_refinement")

class Tool(BaseModel):
    name: str
    description: str
    is_llm: bool
    exists: bool
 
class AddDetailedTaskInput(BaseModel):
    task_id: str
    name: str
    description: str
    tools_required: List[Tool]
    task_dependencies: List[str]
    output_files: List[str]
    required_input_files: List[str]


@mcp.tool(name="output_refined_task_group", description=TASK_GROUP_REFINEMENT_DESCRIPTION, tags={"task_refinement"})
def output_refined_task_group(refined_task_group: List[AddDetailedTaskInput], meta_task_name: str, iteration_index: int) -> str:
    # create the output file path
    output_file_path = os.path.join(SANDBOX_TASK_DIR, meta_task_name, f"{iteration_index}_refined_task_group.json")
    # check if the output file path exists, if not, return an error message. 
    if not os.path.exists(output_file_path):
        return f"Error: The output file path {output_file_path} does not exist. Revise the meta_task_name or iteration_index."
    # overwrite the old task file with the new task file. 
    with open(output_file_path, "w") as f:
        json.dump(refined_task_group, f, indent=4)
    return f"Task file {output_file_path} has been created successfully."


@mcp.tool(name="refine_task_file", description=TASK_REFINEMENT_DESCRIPTION, tags=["task_refinement"])
def refine_task_file(iteration_index: int, new_task: AddDetailedTaskInput, meta_task_name: str) -> str:
    task_id = new_task.task_id # used for overwrite the old task file. 
    output_file_dir = os.path.join(SANDBOX_TASK_DIR, meta_task_name, str(iteration_index))
    # check if the output file path exists, if not, return an error message. 
    if not os.path.exists(output_file_dir):
        return f"Error: The output file path {output_file_dir} does not exist. Revise the task_id or iteration_index."
    # overwrite the old task file with the new task file. 
    with open(os.path.join(output_file_dir, f"{task_id}_refined.json"), "w") as f:
        json.dump(new_task.model_dump(), f, indent=4)
    return f"Task file {os.path.join(output_file_dir, f'{task_id}_refined.json')} has been updated successfully."


if __name__ == "__main__":
    mcp.run(transport="stdio")
 