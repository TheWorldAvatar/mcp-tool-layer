
"""
This server is used to generate task files for the task decomposition agent. 

To make sure the Task Decomposition Agent generates syntactically correct task files, this server is used to generate task files. 
"""
from mcp.server.fastmcp import FastMCP
import subprocess
import json
import os
import logging
from pydantic import BaseModel
from typing import List
import uuid

mcp = FastMCP("task_generation")
logger = logging.getLogger(__name__)
logger.addHandler(logging.FileHandler("task_generation.log"))    


class Tool(BaseModel):
    name: str
    description: str
    exists: bool

class AddTaskInput(BaseModel):
    task_id: str
    name: str
    description: str
    tools: List[Tool]
    expected_output: str
    dependencies: List[str]

@mcp.tool()
def create_new_task(overall_task_name: str, new_task: AddTaskInput) -> str:
    """
    Create a new task with reference to the AddTaskInput object.

    - task_id: Unique identifier for the task, which is a 8 character string.
    - name: Human-readable name of the task.
    - description: Detailed description of what the task goal. 
    - tools: List of tools required for this task, each tool is a Tool object.
    - expected_output: Description of what the task should produce.
    - dependencies: List of task IDs this task depends on, which are the task IDs of the tasks that must be completed before this task can be started.

    Args:
        overall_task_name: The name of the overall task group, which is a string with meaning, in one task decomposition plan, all tasks should share the same overall task name.
        new_task: The input for the task, which is a AddTaskInput object.
    Returns:
        The file path where the new task is created. 
    """

    # Create a new task file
    task_file_path = f"sandbox/tasks/{overall_task_name}/{new_task.task_id}.json"
    if not os.path.exists(os.path.dirname(task_file_path)):
        os.makedirs(os.path.dirname(task_file_path))
    with open(task_file_path, "w") as f:
        json.dump(new_task.model_dump(), f, indent=4)
    return task_file_path

@mcp.tool()
def generate_task_id() -> str:
    """
    Generate a random id for eachtask, which is a 8 character string.

    - The id should be unique for each task.

    Returns:
        The random id for the task.
    """
    return str(uuid.uuid4())[:8]

if __name__ == "__main__":
    mcp.run(transport="stdio")
