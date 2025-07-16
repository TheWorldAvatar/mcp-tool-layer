from fastmcp import FastMCP
import logging
from src.mcp_descriptions.task_generation_coarse import TASK_GENERATION_COARSE_DESCRIPTION, TASK_ID_GENERATION_DESCRIPTION
from typing import List
from models.TaskObjects import AddTaskInput

# Import functions from separated files
from src.mcp_servers.task.operations.task_generation_coarse_operations import (
    create_new_tool_task,
    generate_task_id
)

# -------------------- CONFIG --------------------
mcp = FastMCP("task_operations")
 
# -------------------- TASK GENERATION COARSE TOOLS --------------------

@mcp.tool(name="create_new_tool_task", description=TASK_GENERATION_COARSE_DESCRIPTION, tags=["task_generation_coarse"])
def create_new_tool_task_tool(task_meta_name: str, new_task: AddTaskInput, iteration_number: int) -> str:
    return create_new_tool_task(task_meta_name, new_task, iteration_number)

@mcp.tool(name="generate_task_id", description=TASK_ID_GENERATION_DESCRIPTION, tags=["task_generation_coarse"])
def generate_task_id_tool() -> str:
    return generate_task_id()

# -------------------- MAIN ENTRYPOINT --------------------
if __name__ == "__main__":
    mcp.run(transport="stdio") 