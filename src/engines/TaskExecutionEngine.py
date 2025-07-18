"""
This engine iterates through the tasks according to the dependency, from root task to leaf task. 

According to whether the node contains hypothetical tools, it will use or not use code generation agent.
"""

from src.engines.utils.refined_task_tree import RefinedTaskTree, RefinedTaskNode    
import os
import json
import asyncio
from models.locations import SANDBOX_TASK_DIR, SANDBOX_DATA_DIR
from src.utils.resource_db_operations import ResourceDBOperator
from src.engines.CodeGenerationEngine import code_generation_engine
from src.agents.TasKExecutionAgent import task_execution_agent
from src.utils.global_logger import get_logger

logger = get_logger("engine", "TaskExecutionEngine")        
# All task-related mcp servers are exposed to this engine, including the docker mcp server and generic mcp server.

resource_db_operator = ResourceDBOperator()

async def get_current_resources(meta_task_name: str, iteration_index: int):
    resources = resource_db_operator.get_initial_resource_or_iteration_specific_resource(meta_task_name, iteration_index)
    # only get the files and folders by type
    resources = [resource for resource in resources if resource.type in ["file"]]
    return "\n----\n".join(list(set([str(resource) for resource in resources])))    

async def check_if_require_code_generation(task_node: RefinedTaskNode):
    if task_node.has_hypothetical_tools():
        return True
    return False


async def execute_task_node(task_node: RefinedTaskNode, current_resources: str, task_meta_name: str, iteration_index: int):
    require_code_generation = await check_if_require_code_generation(task_node)
    if require_code_generation:
        code_generation_result = await code_generation_engine(task_meta_name=task_meta_name, iteration_index=iteration_index, task_node=task_node, resources=current_resources)
        # TODO: trigger the code generation agent 
        resource_db_operator.scan_and_register_new_files(folder_path=os.path.join(SANDBOX_DATA_DIR, task_meta_name, str(iteration_index)), task_meta_name=task_meta_name, iteration_index=iteration_index)
        return code_generation_result
    else:
        task_execution_result = await task_execution_agent(meta_instruction=task_node, meta_task_name=task_meta_name, task_node=task_node, resources=current_resources)
        return task_execution_result
  
async def task_execution_engine(task_meta_name: str, iteration_index: int, task_node: str):
    # 1. Get the <index>_refined_task_group.json file
    refined_task_group_file_path = os.path.join(SANDBOX_TASK_DIR, task_meta_name, f"{str(iteration_index)}_refined_task_group.json")

    # 2. Get the task_node from the refined_task_group_file_path
    with open(refined_task_group_file_path, "r") as f:
        refined_task_group = json.load(f)
        refined_task_tree = RefinedTaskTree(refined_task_group)

        for task_node in refined_task_tree.get_dependency_ordered_task_nodes():
            current_resources = await get_current_resources(task_meta_name, iteration_index)
            await execute_task_node(task_node, current_resources, task_meta_name, iteration_index)
             


if __name__ == "__main__":
    asyncio.run(task_execution_engine(task_meta_name="jiying", iteration_index=1, task_node=""))
