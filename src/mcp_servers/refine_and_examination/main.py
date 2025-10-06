from fastmcp import FastMCP
from src.mcp_descriptions.task_refinement import TASK_GROUP_REFINEMENT_DESCRIPTION
from src.mcp_descriptions.task_generation_coarse import TASK_INDEX_SELECTION_DESCRIPTION
from typing import List
from models.TaskObjects import AddTaskInput

# Import functions from separated files
from src.mcp_servers.refine_and_examination.operations.task_refinement_operations import (
    output_refined_task_group
)

from src.mcp_servers.refine_and_examination.operations.task_plan_selection_operations import (
    output_selected_task_index
)
from src.utils.global_logger import get_logger, mcp_tool_logger

# -------------------- CONFIG --------------------
mcp = FastMCP("refine_and_examination_operations")
logger = get_logger("mcp_server", "refine_and_examination_main")
 
# -------------------- TASK REFINEMENT TOOLS --------------------

@mcp.tool(name="output_refined_task_group", description=TASK_GROUP_REFINEMENT_DESCRIPTION, tags=["task_refinement"])
@mcp_tool_logger
def output_refined_task_group_tool(refined_task_group: List[AddTaskInput], meta_task_name: str, iteration_index: int) -> str:
    return output_refined_task_group(refined_task_group, meta_task_name, iteration_index)

# -------------------- TASK GENERATION COARSE TOOLS --------------------

@mcp.tool(name="output_selected_task_index", description=TASK_INDEX_SELECTION_DESCRIPTION, tags=["task_generation_coarse"])
@mcp_tool_logger
def output_selected_task_index_tool(meta_task_name: str, selected_list_of_task_index: List[int]) -> str:
    return output_selected_task_index(meta_task_name, selected_list_of_task_index)

# -------------------- MAIN ENTRYPOINT --------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
