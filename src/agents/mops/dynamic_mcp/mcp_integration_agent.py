from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
import asyncio

iteration_index = 3 # for synthesis steps

ontology = ""
with open('data/ontologies/T-Box.ttl', 'r') as file:
    ontology = file.read()
    
script = ""
with open('sandbox/code/mcp_creation/{iteration_index}/mcp_creation.py', 'r') as file:
    script = file.read().replace("{", "{{").replace("}", "}}")

MCP_INTEGRATION_PROMPT = f"""
Your task is to integrate the MCP tools into a single MCP server. 

The MCP server always follows the following structure:

- mcp_creation.py: this is where you can find all the functions you need, which is already created by a previous agent. The file name is always mcp_creation.py.
- main.py: this is the script you need to create, which include all the functions in the mcp_creation.py file, and wrapped up as a single MCP server. 

Highest Priority: 

** The core design principle **: 

The achieve a script that later helps the agents to build up a knowledge graph that is as complete as possible.

We will need to design the functions that it follows "coarse to fine, bigger to smaller" principle.

For example, the function should guide the agent to create all the cities first, then for each city, 
create the buildings, then for each building, create the rooms, etc. This is very important. 



Here is a template for the main.py file:

```python
from fastmcp import FastMCP
from .mcp_creation import <func1, func2, ...>
from src.utils.global_logger import get_logger, mcp_tool_logger

mcp = FastMCP(name="<meta_task_name>")

@mcp.prompt(name="instruction")
def instruction_prompt():
    return "<an overall text-based instruction for the MCP server, including introduction of the MCP server, and what tools do what>
    + <a comprehensive calling sequence of the tools for a complete knowledge graph creation>"

@mcp.tool(name="<tool_name>", description="<tool_description> + <instruction_for_next_function>")
@mcp_tool_logger
def <tool_name>(<tool_parameters>) -> str:
    return <tool_name>(<tool_parameters>)

@mcp.tool(name="<tool_name>", description="<tool_description> + <instruction_for_next_function>")
@mcp_tool_logger
def <tool_name>(<tool_parameters>) -> str:
    return <tool_name>(<tool_parameters>)

... 

if __name__ == "__main__":
    mcp.run(transport="stdio")

Note the following:

- **Critical:** In principle, we expect the agents that use this MCP server to call all the functions, so that a complete 
knowledge graph can be created. As a result, in your instruction prompt and function descriptions, you will need to 
explicity instruct what other functions needs to be called after calling the current function. 

For example, for create_building function, you should instruct that "connect_has_floor_number", "connect_building_location" functions needs to be called after calling the create_building function.

- **Critical:** Read rdf:comments carefully and create functions accordingly, there might be extremely important information. They should be good reference for you to add extra instructions in the prompt and function descriptions. 
- **Critical:** Never use }} or {{ in the function description or instruction prompt, this will lead to the complete failure of the MCP server.
- **Critical:** If you include hash-based file-based object memory, you will need to let the agent know which hash to use across the task. 
- **Critical:** Pay special attention to rdfs:comment annotations in the ontology - use these comments to create clear, informative MCP tool descriptions and instruction prompts that reflect the semantic meaning and purpose of each function.
- You will not be able to run this script, so you don't have to verify whether the code can be executed successfully.
- The mcp_creation.py is always in the same directory as the main.py file, so write your imports accordingly. 
- The mcp_creation.py file uses a file-based object memory for each function to resume creation and update of the object. Given the hash of the object, the function can resume creation and update of the object.
- This is the right way to import the mcp_creation.py file: from .mcp_creation import <func1, func2, ...>
- In the instructions, you should remind the agent that label attributes preferably use original text from the paper. 

Meta data: 

- meta_task_name: mcp_creation
- task_index: {iteration_index}
- script_name: main.py

Here is the mcp_creation.py file:

```python
{script}
```

Here is the T-Box file:

```turtle
{ontology}
```

"""

async def mcp_integration_agent():
    """
    This agent integrates the MCPs.
    """
    model_config = ModelConfig()
    mcp_tools = ["sandbox", "generic_operations"]
    agent = BaseAgent(model_name="gpt-5", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="mcp_creation_mcp_configs.json")    
    response, metadata = await agent.run(MCP_INTEGRATION_PROMPT.format(iteration_index=iteration_index))
    return response


if __name__ == "__main__":
    response = asyncio.run(mcp_integration_agent())
    print(response)