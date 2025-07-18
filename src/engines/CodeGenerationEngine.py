import os
import asyncio
from src.agents.CodeGenerationAgent import code_generation_agent
from src.utils.resource_db_operations import ResourceDBOperator
from models.locations import SANDBOX_DATA_DIR
from models.Resource import Resource

resource_db_operator = ResourceDBOperator()

async def code_generation_engine(
    task_meta_name: str,
    iteration_index: int,
    task_node: str,
    resources: str
) -> str:
    """
    Execute code generation for a single RefinedTaskNode.

    Args:
        task_meta_name (str): The meta task name.
        iteration_index (int): The iteration index for the task group.
        task_node (str): The node to generate code for.

    Returns:
        str: The code generation agent's response.
    """

    output_folder = os.path.join(SANDBOX_DATA_DIR, task_meta_name, str(iteration_index))
    os.makedirs(output_folder, exist_ok=True)

    # Run the code generation agent for this node
    code_generation_result = await code_generation_agent(
        str(task_node), task_meta_name, iteration_index, resources
    )
    return code_generation_result

 