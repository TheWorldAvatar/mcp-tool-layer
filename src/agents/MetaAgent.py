from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
import asyncio

INSTRUCTION_PROMPT_GOLD = """
I want you to create a set of python script that use cclib to parse gaussian log file and create csv files. 

The problem is that we need to represent the data in gaussian log file in csv format. However, a flattened table will mis-represent the data. 

As a result, we will need multiple csv files to represent the data. 

To do that, we need to create a set of python scripts that use cclib to parse gaussian log file and create csv files. 

Each csv file will represent a different aspect of the data. 

The gaussian log file is at /data/test/benzene.log. Try to read it and plan how you will create the python scripts to parse the data and create csv files via the scripts. 

After you created your python scripts, use the sandbox to test them and actually run the scripts. 

**Important**: In this turn, you don't need to actually do the work, read the gaussian log file and give me the detailed plan only, including:

- What aspects of the data you will need to represent in the csv files?
- How many csv files you will need to create and what information does each csv file contain?
- What tools do you plan to call and what are the arguments you will pass to them?
- In what order you will call the tools?
- What MCP servers you will use and what specific functions you will call?

Yield a markdown file that contains the plan named "task_decomposition.md", output the file at /data/task_decomposition.md.
"""

INSTRUCTION_PROMPT_SHIT_1 = """
Create a python script in /sandbox/script.py that prints "Hello, world! Hello, world!"

Then run the script in the sandbox and return the output.
"""
INSTRUCTION_PROMPT_SHIT = """
Run print("Hello, world!") in the sandbox.
"""

async def main():

    model_config = ModelConfig()
    mcp_tools = ["agent_standalone", "filesystem"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools)
    response, metadata = await agent.run(INSTRUCTION_PROMPT_GOLD, recursion_limit=200)
    print(response)
 

if __name__ == "__main__":
    asyncio.run(main())





