from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
import asyncio

INSTRUCTION_PROMPT = """
Create a docker container with python 3.11, and mount local directory sandbox to /sandbox

No ports are needed. Give it a random name, like "python3.11-tom-jerry. But you need to come up with the new names.

Then, execute a python code that writes a txt file named "hello.txt" in the sandbox/data directory. 

Also execute the existing python code sandbox/code/hello_world.py

Then, install python library cclib and execute the python code sandbox/code/cclib_test.py
"""


async def main():
    model_config = ModelConfig()
    mcp_tools = ["filesystem", "docker"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools)
    response, metadata = await agent.run(INSTRUCTION_PROMPT)
    print(response)
 

if __name__ == "__main__":
    asyncio.run(main())





