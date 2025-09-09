from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
import asyncio

script = ""
with open('sandbox/code/mcp_creation/0/mcp_creation.py', 'r') as file:
    script = file.read().replace("{", "{{").replace("}", "}}")

MCP_INTEGRATION_PROMPT = f"""
Your task is to integrate the MCP tools into a single MCP server. 

The MCP server always follows the following structure:

- mcp_creation.py: this is where you can find all the functions you need, which is already created by a previous agent. The file name is always mcp_creation.py.
- main.py: this is the script you need to create, which include all the functions in the mcp_creation.py file, and wrapped up as a single MCP server. 

Here is a template for the main.py file:

```python
from fastmcp import FastMCP
from .mcp_creation import <func1, func2, ...>
from src.utils.global_logger import get_logger, mcp_tool_logger

mcp = FastMCP(name="<meta_task_name>")

@mcp.prompt(name="instruction")
def instruction_prompt():
    return "<an overall text-based instruction for the MCP server, including introduction of the MCP server, and what tools do what>"

@mcp.tool(name="<tool_name>", description="<tool_description>")
@mcp_tool_logger
def <tool_name>(<tool_parameters>) -> str:
    return <tool_name>(<tool_parameters>)

@mcp.tool(name="<tool_name>", description="<tool_description>")
@mcp_tool_logger
def <tool_name>(<tool_parameters>) -> str:
    return <tool_name>(<tool_parameters>)

... 

if __name__ == "__main__":
    mcp.run(transport="stdio")


Note the following:

- **Critical:** Never use }} or {{ in the function description or instruction prompt, this will lead to the complete failure of the MCP server.
- **Critical:** If you include hash-based file-based object memory, you will need to let the agent know which hash to use across the task. 
- You will not be able to run this script, so you don't have to verify whether the code can be executed successfully.
- The mcp_creation.py is always in the same directory as the main.py file, so write your imports accordingly. 
- The mcp_creation.py file uses a file-based object memory for each function to resume creation and update of the object. Given the hash of the object, the function can resume creation and update of the object.
- This is the right way to import the mcp_creation.py file: from .mcp_creation import <func1, func2, ...>

Meta data: 

- meta_task_name: mcp_creation
- task_index: 0
- script_name: main.py

Here is the mcp_creation.py file:

```python
{script}
```

"""

async def mcp_integration_agent():
    """
    This agent integrates the MCPs.
    """
    model_config = ModelConfig()
    mcp_tools = ["sandbox", "generic_operations"]
    agent = BaseAgent(model_name="gpt-5", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="mcp_creation_mcp_configs.json")    
    response, metadata = await agent.run(MCP_INTEGRATION_PROMPT)
    return response


if __name__ == "__main__":
    response = asyncio.run(mcp_integration_agent())
    print(response)