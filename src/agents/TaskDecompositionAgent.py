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
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig


INSTRUCTION_PROMPT_GOLD = """

I have a gaussian output log file at /data/test/benzene.log. This file contains the output of a gaussian calculation. 

My system, the world avatar, uses semantic technologies to represent the data and support queries across data from different domains. More information can be found with "stack_operations" tool. 

In this turn, I want you to integrate the data from the gaussian log file into my system. 

**Important**: In this turn, you don't need to actually do the work and give me the detailed plan only, taking consideration of the tools you have access to.

Keep in mind a strategy that: 

1. Read the tool descriptions carefully, where you can learn how the underlying system works. 
2. Don't be afraid to include hypothetical tools, which will be created for the tasks
3. Use tools instead of using LLMs to deal with files.
4. Break the tasks in to as small as possible subtasks, each subtask should be yield a single task file. 


Create task files with task_generation tool. 

"""
 

async def main():
    model_config = ModelConfig()
    mcp_tools = ["all"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools)
    response, metadata = await agent.run(INSTRUCTION_PROMPT_GOLD, recursion_limit=200)
    print(response)
 

if __name__ == "__main__":
    asyncio.run(main())











