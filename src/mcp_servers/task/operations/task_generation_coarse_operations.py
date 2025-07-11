"""
Task generation coarse operations
Functions for generating task files for the task decomposition agent.
"""

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
from models.TaskObjects import AddTaskInput
from filelock import FileLock
import time  # optional: useful for debugging lock wait

def create_new_tool_task(task_meta_name: str, new_task: AddTaskInput, iteration_number: int) -> str:
    task_file_dir = os.path.join(SANDBOX_TASK_DIR, task_meta_name, str(iteration_number))
    task_file_path = os.path.join(task_file_dir, f"{new_task.task_id}.json")
    os.makedirs(task_file_dir, exist_ok=True)

    lock_path = task_file_path + ".lock"
    lock = FileLock(lock_path, timeout=10)  # optional timeout to avoid deadlock

    with lock:
        with open(task_file_path, "w", encoding="utf-8") as f:
            json.dump(new_task.model_dump(), f, indent=4)

    return task_file_path

def generate_task_id() -> str:
    return str(uuid.uuid4())[:6]

def output_selected_task_index(meta_task_name: str, selected_task_index: List[int]) -> List[int]:
    # write the selected task index to a file
    _output_selected_task_index(meta_task_name, selected_task_index)
    return f"Selected task index {selected_task_index} has been output to {os.path.join(SANDBOX_TASK_DIR, meta_task_name, 'selected_task_index.json')}"

def _output_selected_task_index(meta_task_name: str, selected_task_index: List[int]) -> List[int]:
    with open(os.path.join(SANDBOX_TASK_DIR, meta_task_name, "selected_task_index.json"), "w") as f:
        json.dump(selected_task_index, f, indent=4)
    return selected_task_index 