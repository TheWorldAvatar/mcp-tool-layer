from fastmcp import FastMCP
import logging
from src.mcp_descriptions.task_refinement import TASK_REFINEMENT_DESCRIPTION, TASK_GROUP_REFINEMENT_DESCRIPTION
from src.mcp_descriptions.task_generation_coarse import TASK_GENERATION_COARSE_DESCRIPTION, TASK_ID_GENERATION_DESCRIPTION, TASK_INDEX_SELECTION_DESCRIPTION
from typing import List
from models.TaskObjects import AddDetailedTaskInput, AddTaskInput

# Import functions from separated files
from src.mcp_servers.task.operations.task_refinement_operations import (
    output_refined_task_group
)

from src.mcp_servers.task.operations.task_generation_coarse_operations import (
    create_new_tool_task,
    generate_task_id,
    output_selected_task_index
)

# -------------------- CONFIG --------------------
mcp = FastMCP("task_operations")
 
# -------------------- TASK REFINEMENT TOOLS --------------------

@mcp.tool(name="output_refined_task_group", description=TASK_GROUP_REFINEMENT_DESCRIPTION, tags=["task_refinement"])
def output_refined_task_group_tool(refined_task_group: List[AddDetailedTaskInput], meta_task_name: str, iteration_index: int) -> str:
    return output_refined_task_group(refined_task_group, meta_task_name, iteration_index)

# -------------------- TASK GENERATION COARSE TOOLS --------------------

@mcp.tool(name="create_new_tool_task", description=TASK_GENERATION_COARSE_DESCRIPTION, tags=["task_generation_coarse"])
def create_new_tool_task_tool(task_meta_name: str, new_task: AddTaskInput, iteration_number: int) -> str:
    return create_new_tool_task(task_meta_name, new_task, iteration_number)

@mcp.tool(name="generate_task_id", description=TASK_ID_GENERATION_DESCRIPTION, tags=["task_generation_coarse"])
def generate_task_id_tool() -> str:
    return generate_task_id()

@mcp.tool(name="output_selected_task_index", description=TASK_INDEX_SELECTION_DESCRIPTION, tags=["task_generation_coarse"])
def output_selected_task_index_tool(meta_task_name: str, selected_task_index: List[int]) -> List[int]:
    return output_selected_task_index(meta_task_name, selected_task_index)

# -------------------- MAIN ENTRYPOINT --------------------
if __name__ == "__main__":
    mcp.run(transport="stdio") 