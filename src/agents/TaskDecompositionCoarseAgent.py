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
import os
import asyncio
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import DATA_TEST_DIR, DATA_LOG_DIR
from scripts.clean_task_dir import clean_task_dir
from datetime import datetime


async def main(model_name: str, time_stamp: str, iteration: int):


    INSTRUCTION_PROMPT_GOLD = """

    I have a gaussian output log file at data/test/benzene.log. This file contains the output of a gaussian calculation. 
    I want you to integrate the data from the gaussian log file into my system stack. Make sure the data is integrated into the stack. 

    **Important**: 

    1. You are creating the task plan, not actually doing the work. 
    2. You should consider all the tools you have access to, and make sure the plan is compatible with the existing system. The tools are critical for the plan, and provide a lot of context for the planning.
    3. A following agent will refine the task plan, and your goal is to come up with task steps that reaches the final goal and fleasible. 
    4. Avoid use filesystem tools to read any file in your plan, unless you have no other choice (but you can read the file content in this turn). 
    5. Avoid using LLMs to directly generate file content, unless you have no other choice (ttl file creation is an exception).

    The overall task name is %s

    Create task files with task_generation_coarse tool. (You need to create the task files instead of telling me plan in response)

    Review your plan and make sure it reaches the final goal. It is fine that you missed details, but you must make sure the plan is completed.
    """ % (f"{model_name}_{iteration}")
 

    # clear the task directory before start
    model_config = ModelConfig()
    mcp_tools = ["all"]

    agent = BaseAgent(model_name=model_name, model_config=model_config, remote_model=True, mcp_tools=mcp_tools)
    response, metadata = await agent.run(INSTRUCTION_PROMPT_GOLD, recursion_limit=500)
    
    output_dir = os.path.join(DATA_LOG_DIR, f"task_decomposition_coarse_{model_name}_{time_stamp}") 
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # write the response to .md file with f"task_decomposition_coarse_{iteration}.md"
    with open(os.path.join(output_dir, f"{iteration}.md"), "w") as f:
        f.write(response)
 
 