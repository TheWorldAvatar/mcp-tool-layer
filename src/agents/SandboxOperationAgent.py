from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
import asyncio
from src.prompts.SandboxPrompts import BASIC_SANDBOX_TEST_PROMPT
 
async def basic_sandbox_test():
    """
    This agent tests the basic sandbox operation. 

    Including 
    1. Create a docker container with python 3.11, and mount local directory sandbox to /sandbox
    2. No ports are needed. Give it a random name, like "python3.11-tom-jerry. But you need to come up with the new names.
    3. Then, execute a python code that writes a txt file named "hello.txt" in the sandbox/data directory. 
    4. Also execute the existing python code sandbox/code/hello_world.py
    5. Then, install python library cclib and execute the python code sandbox/code/cclib_test.py
    """
    model_config = ModelConfig()
    mcp_tools = ["filesystem", "sandbox"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools)
    response, metadata = await agent.run(BASIC_SANDBOX_TEST_PROMPT)
    print(response)
 
async def python_script_creation_agent(script_name: str, sandbox_name: str, meta_instruction: str):
    """
    This agent creates a python script in the sandbox/code directory and configures the sandbox environment, attempts to execute it until the satisfied with the output.

    Args:
        script_name: the name of the script
        script_content: the content of the script
        meta_instruction: the meta instruction

    Returns:
        The script file in the sandbox/code directory.
        The sandbox name.
    """
    # TODO: Implement this agent
    pass 



if __name__ == "__main__":
    asyncio.run(basic_sandbox_test())





