"""
Task refinement operations
Functions for refining and outputting task files for the task decomposition agent.
"""

import subprocess
import json
import os
from pydantic import BaseModel
from typing import List
import uuid
import shutil
from models.locations import SANDBOX_TASK_DIR, SANDBOX_TASK_ARCHIVE_DIR, DATA_LOG_DIR
from datetime import datetime
from models.TaskObjects import AddTaskInput


def output_refined_task_group(refined_task_group: List[AddTaskInput], meta_task_name: str, iteration_index: int) -> str:
    # create the output file path
    output_file_path = os.path.join(SANDBOX_TASK_DIR, meta_task_name, f"{str(iteration_index)}_refined_task_group.json")

    # check whether the input is valid, otherwise return the error message
    for task in refined_task_group:
        if not task.task_id:
            return f"Error: Task ID is required for each task."
        if not task.name:
            return f"Error: Task name is required for each task."
        if not task.description:
            return f"Error: Task description is required for each task."
        if not isinstance(task.tools_required, list):
            return f"Error: Task tools_required must be a list for each task."
        if task.tools_required is None:
            return f"Error: Task tools_required is required for each task."
        if not isinstance(task.dependencies, list):
            return f"Error: Task dependencies must be a list for each task."
        if task.dependencies is None:
            return f"Error: Task dependencies is required for each task."


        # try model_dump()
        try:
            task.model_dump()
        except Exception as e:
            return f"Error: {e}"


    # overwrite the old task file with the new task file. 
    with open(output_file_path, "w") as f:
        # Convert each AddDetailedTaskInput model to dict using model_dump()
        refined_task_group_dict = [task.model_dump() for task in refined_task_group]
        json.dump(refined_task_group_dict, f, indent=4)
    return f"Task file {output_file_path} has been created successfully." 


    
if __name__ == "__main__":
    refined_task_group = [AddTaskInput(task_id="1", name="Task 1", description="Task 1 description", tools_required=[], dependencies=[])]
    output_refined_task_group(refined_task_group, "test", 1)