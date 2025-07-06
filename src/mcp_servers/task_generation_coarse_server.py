
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
from models.locations import SANDBOX_TASK_DIR, SANDBOX_TASK_ARCHIVE_DIR, DATA_LOG_DIR, TRACE_FILE_PATH
from datetime import datetime
from src.mcp_descriptions.task_generation_coarse import TASK_GENERATION_COARSE_DESCRIPTION, TASK_ID_GENERATION_DESCRIPTION, TASK_INDEX_SELECTION_DESCRIPTION    
from models.TaskObjects import AddTaskInput

mcp = FastMCP("task_generation_coarse") 
 

def update_task_tracing(task_file_path: str, task_meta_name: str, new_task):
    """
    Update the task_tracing.json file to reflect the new subtask created.
    
    Args:
        overall_task_name (str): Name of the task group
        new_task (Task): A task object with at least `task_id`
    """
    # Load or initialize the task tracing registry
    if os.path.exists(TRACE_FILE_PATH):
        with open(TRACE_FILE_PATH, "r") as f:
            task_registry = json.load(f)
    else:
        task_registry = {"task_groups": []}

    # Find or create the task group entry
    group = next((g for g in task_registry["task_groups"] if g["name"] == task_meta_name), None)
    if group is None:
        group = {
            "name": task_meta_name,
            "current_step": "task-decomposition",  # default, can be modified later
            "subtasks": []
        }
        task_registry["task_groups"].append(group)

    # Add the new subtask if not already recorded
    if not any(t["task_id"] == new_task.task_id for t in group["subtasks"]):
        group["subtasks"].append({
            "task_id": new_task.task_id,
            "file_path": task_file_path
        })

    # Save updated registry
    with open(TRACE_FILE_PATH, "w") as f:
        json.dump(task_registry, f, indent=4)

    return task_file_path

@mcp.tool(name="create_new_tool_task", description=TASK_GENERATION_COARSE_DESCRIPTION, tags=["task_generation_coarse"])
def create_new_tool_task(task_meta_name: str, new_task: AddTaskInput, iteration_number: int) -> str:
    # Create a new task file
    task_file_dir = os.path.join(SANDBOX_TASK_DIR, task_meta_name, str(iteration_number))
    task_file_path = f"{task_file_dir}/{new_task.task_id}.json"
    if not os.path.exists(task_file_dir):
        os.makedirs(task_file_dir)
    with open(task_file_path, "w") as f:
        json.dump(new_task.model_dump(), f, indent=4)

    # update the task_tracing.json file
    update_task_tracing(task_file_path, task_meta_name, new_task)    

    return task_file_path

@mcp.tool(name="generate_task_id", description=TASK_ID_GENERATION_DESCRIPTION, tags=["task_generation_coarse"])
def generate_task_id() -> str:
    return str(uuid.uuid4())[:6]


@mcp.tool(name="output_selected_task_index", description=TASK_INDEX_SELECTION_DESCRIPTION, tags=["task_generation_coarse"])
def output_selected_task_index(meta_task_name: str, selected_task_index: List[int]) -> List[int]:
    # write the selected task index to a file
    _output_selected_task_index(meta_task_name, selected_task_index)
    return f"Selected task index {selected_task_index} has been output to {os.path.join(SANDBOX_TASK_DIR, meta_task_name, 'selected_task_index.json')}"

def _output_selected_task_index(meta_task_name: str, selected_task_index: List[int]) -> List[int]:
    with open(os.path.join(SANDBOX_TASK_DIR, meta_task_name, "selected_task_index.json"), "w") as f:
        json.dump(selected_task_index, f, indent=4)
    return selected_task_index

if __name__ == "__main__":
    mcp.run(transport="stdio")
    # _output_selected_task_index(meta_task_name="jiying", selected_task_index=[0, 1])