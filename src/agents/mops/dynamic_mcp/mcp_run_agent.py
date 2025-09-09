from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
import asyncio

MCP_RUN_PROMPT = """
Create very simple knowledge graph using the MCP server given. Make sure you output the file. 

It neesd to include the following: 

- A synthesis
- A supplier (with name "Alderich")
- A chemical input
- A chemical output
- A vessel
- A vessel type
- A vessel environment
- A step
- A stir step
- A heatchill step
- A evaporate step
- A sonicate step
- A dissolve step
- A crystallize step

And make connection between the objects.

Tell me the following:

1. Is there any issues with gettinng the hash? 
2. Did you use the same hash across the task?
3. Are there any problem adding information to the knowledge graph. 

"""

async def mcp_run_agent():
    """
    This agent runs the MCP server.
    """
    model_config = ModelConfig()
    mcp_tools = ["llm_created_mcp"]
    agent = BaseAgent(model_name="gpt-4o", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="run_created_mcp.json")
    response, metadata = await agent.run(MCP_RUN_PROMPT, recursion_limit=200)
    return response


if __name__ == "__main__":
    response = asyncio.run(mcp_run_agent())
    print(response)

