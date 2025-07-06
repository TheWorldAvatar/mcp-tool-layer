
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
from src.mcp_descriptions.task_generation import TASK_GENERATION_DESCRIPTION, TASK_ID_GENERATION_DESCRIPTION
from models.TaskObjects import AddTaskInput

mcp = FastMCP("task_generation")
logger = logging.getLogger(__name__)
logger.addHandler(logging.FileHandler("task_generation.log"))    


 


@mcp.tool(name="create_new_tool_task", description=TASK_GENERATION_DESCRIPTION, tags=["task_generation"])
def create_new_tool_task(overall_task_name: str, new_task: AddTaskInput) -> str:
    # Create a new task file
    task_file_path = f"{SANDBOX_TASK_DIR}/{overall_task_name}/{new_task.task_id}.json"
    if not os.path.exists(os.path.dirname(task_file_path)):
        os.makedirs(os.path.dirname(task_file_path))
    with open(task_file_path, "w") as f:
        json.dump(new_task, f, indent=4)
    return task_file_path

@mcp.tool(name="generate_task_id", description=TASK_ID_GENERATION_DESCRIPTION, tags=["task_generation"])
def generate_task_id() -> str:
    return str(uuid.uuid4())[:8]




if __name__ == "__main__":
    mcp.run(transport="stdio")
    # archive_completed_task()
