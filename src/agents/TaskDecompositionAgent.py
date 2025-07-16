"""
Task Decomposition Agent:

- This agent takes abstract tasks and breaks them down into smaller, more manageable subtasks.
- It should has access to all the tools registered in the system, and understand what is within the systems capabilities. 
- It should yield the subtasks into files, which structurally and explicitly defines the following: 
    - The name of the tasks 
    - The tools should be exposed to the tasks and the tool descriptions 
    - The dependencies between the tasks, especially whether the subtasks are dependent on the previous subtasks. 
    - The expected output of the tasks, and what role does this task play in the overall plan. 
"""

import asyncio
import os
import time
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import DATA_TEST_DIR, DATA_GENERIC_DIR, SANDBOX_TASK_DIR
from scripts.clean_task_dir import clean_task_dir
from src.prompts.DecompositionPrompts import INSTRUCTION_GENERIC_PROMPT
from src.utils.file_management import safe_handle_file_write, fuzzy_repo_file_search, read_file_content_from_uri
from src.utils.resource_db_operations import ResourceDBOperator

resource_db_operator = ResourceDBOperator() 

 

async def task_decomposition_agent(task_meta_name: str, meta_instruction: str, iteration_number: int):
    """
    This agent decomposes a task into smaller subtasks. It iterates n times and hence produce n task groups as candidates. 

    Args:
        task_meta_name: the name of the task
        meta_instruction: the meta instruction
        iteration_number: the number of iterations

    Returns:
        Write each step into a json file with the task id, in /sandbox/tasks/{task_meta_name}/{iteration}/task_files.json
        Write the task summary in markdown format to the /sandbox/tasks/{task_meta_name}/{iteration}/task_summary.md
    """

    data_sniffing_report = read_file_content_from_uri(
        fuzzy_repo_file_search(os.path.join(SANDBOX_TASK_DIR, task_meta_name, "data_sniffing_report.md")).uri
    )
 


 
    model_config = ModelConfig(temperature=0.4, top_p=0.02)
    mcp_tools = ["stack", "task"]
    for iteration in range(iteration_number):
        agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="task_decomposition_mcp_configs.json")
        # sleep for 10 seconds
        instruction = INSTRUCTION_GENERIC_PROMPT.format(meta_instruction=meta_instruction, 
        data_sniffing_report=data_sniffing_report, 
        task_meta_name=task_meta_name, 
        iteration_number=iteration)
        response, metadata = await agent.run(instruction, recursion_limit=500)     
        print(response)

        task_summary_path = f"file://{os.path.join(SANDBOX_TASK_DIR, task_meta_name, str(iteration), 'task_summary.md')}"
        safe_handle_file_write(task_summary_path, response)
    

if __name__ == "__main__":
    response = asyncio.run(task_decomposition_agent(task_meta_name="jiying", meta_instruction="", iteration_number=1))
    print(response)
 








