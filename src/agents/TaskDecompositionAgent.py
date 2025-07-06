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
from src.prompts.DecompositionPrompts import INSTRUCTION_GENERIC_PROMPT, INSTRUCTION_DATA_SNIFFING_PROMPT

 

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

    # clear the task directory before start
    # clean_task_dir()

        # load the data sniffing report
    with open(os.path.join(SANDBOX_TASK_DIR, task_meta_name, "data_sniffing_report.md"), "r") as f:
        data_sniffing_report = f.read()

    model_config = ModelConfig()
    mcp_tools = ["all"]
    for iteration in range(iteration_number):
        agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="task_decomposition_mcp_configs.json")
        # sleep for 10 seconds
        instruction = INSTRUCTION_GENERIC_PROMPT.format(meta_instruction=meta_instruction, 
        data_sniffing_report=data_sniffing_report, 
        task_meta_name=task_meta_name, 
        iteration_number=iteration)
        response, metadata = await agent.run(instruction, recursion_limit=500)     
        print(response)

        output_dir = f"sandbox/tasks/{task_meta_name}/{str(iteration)}"
        os.makedirs(output_dir, exist_ok=True)
        with open(f"{output_dir}/task_summary.md", "w") as f:
            f.write(response)
    

async def data_sniffing_agent(folder_path: str, task_meta_name: str):
    """
    This agent sniff the data in the folder and generate a data sniffing report, summarizing the inital data provided. 

    Args:
        folder_path: the path to the data folder
        task_meta_name: the name of the task

    Returns:
        Write the data sniffing report in markdown format to the /sandbox/tasks/{task_meta_name}/data_sniffing_report.md
        Write a resources.json file in the /sandbox/tasks/{task_meta_name}/resources.json, which is a structured output. 
    """
    model_config = ModelConfig()    
    mcp_set_name = "pretask_mcp_configs.json"
    mcp_tools = ["generic_file_operations", "resource_registration"]
    instruction = INSTRUCTION_DATA_SNIFFING_PROMPT.format(folder_path=folder_path, task_meta_name=task_meta_name)
    agent = BaseAgent(model_name="gpt-4.1-mini", remote_model=True, mcp_set_name=mcp_set_name, mcp_tools=mcp_tools, model_config=model_config)
    response, metadata = await agent.run(instruction, recursion_limit=300)
    print(response)


if __name__ == "__main__":
    response = asyncio.run(task_decomposition_agent(task_meta_name="patrick", data_folder_path="", meta_instruction="", iteration_number=1))
    print(response)
    # asyncio.run(data_sniffing_agent(folder_path="/data/generic_data/jiying", task_meta_name="jiying"))
 








