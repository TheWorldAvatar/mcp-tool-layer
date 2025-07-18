"""
Task generation coarse operations
Functions for generating task files for the task decomposition agent.
"""

import subprocess
import json
import os
from pydantic import BaseModel
from typing import List
import uuid
import shutil
from models.locations import SANDBOX_TASK_DIR, SANDBOX_TASK_ARCHIVE_DIR, DATA_LOG_DIR, TRACE_FILE_PATH
from datetime import datetime
from models.TaskObjects import AddTaskInput
from src.utils.file_management import safe_write_json
from src.utils.resource_db_operations import ResourceDBOperator

db_operator = ResourceDBOperator()

def create_new_tool_task(task_meta_name: str, new_task: AddTaskInput, iteration_number: int) -> str:
    task_file_dir = os.path.join(SANDBOX_TASK_DIR, task_meta_name, str(iteration_number))
    task_file_uri = f"file://{os.path.join(task_file_dir, f'{new_task.task_id}.json')}"
    write_result = safe_write_json(task_file_uri, new_task.model_dump())
    return f"Task {new_task.task_id} output status: {write_result}"

def generate_task_id() -> str:
    return str(uuid.uuid4())[:6]

if __name__ == "__main__":
    from models.TaskObjects import Tool