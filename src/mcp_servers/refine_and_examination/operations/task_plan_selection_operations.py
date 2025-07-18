"""
Task plan selection operations
Functions for selecting task plans for the task decomposition agent.
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

def output_selected_task_index(meta_task_name: str, selected_list_of_task_index: List[int]) -> str:
    # write the selected task index to a file
    
    # check whether the selected task index is valid and == 2 
    if len(selected_list_of_task_index) != 2:
        return f"Error: Selected task index must be a list of length 2. The selected task index is {selected_list_of_task_index}"

    # check the selected task index is in the range of 0 to 2, this is not a sorted list
    for index in selected_list_of_task_index:
        if index < 0 or index > 2:
            return f"Error: Selected task index must be in the range of 0 to 2. The selected task index is {selected_list_of_task_index}"

    selected_task_index_path = f"file://{os.path.join(SANDBOX_TASK_DIR, meta_task_name, 'selected_task_index.json')}"
    write_result = safe_write_json(selected_task_index_path, selected_list_of_task_index)
    return f"Selected task index {selected_list_of_task_index} output status: {write_result}" 