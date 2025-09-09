from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
import asyncio


script = ""
with open('sandbox/code/mcp_creation/0/mcp_creation.py', 'r') as file:
    script = file.read().replace("{", "{{").replace("}", "}}")
 
ontology = ""
with open('data/ontologies/output.ttl', 'r') as file:
    ontology = file.read()


MCP_TEST_PROMPT = f"""

Your task is to create one-file unit test script for a given python script. 

The purpose of the python script is to provide atomic functions to build up a knowledge graph.

The python script is created by a previous agent according to an ontology schema. 

As a result, the unit test script should focus on the following:

1. Whether the python script can be executed successfully.
2. Does the output of the python script fully represent the ontology schema, are there anything missing from the ontology schema or wrong. 
3. Is the code well-organized, readable, and maintainable, and most importantly, atomic enough. 

In your final response, you should create a report of the unit test script for the 3 points above.

## meta data: 

- meta_task_name: mcp_creation
- task_index: 0
- script_name: test_mcp_creation.py

You should use conda environment name: mcp_creation

Here is the python script: 
{script}

Here is the ontology schema: 
{ontology}
"""

async def mcp_test_agent():
    """
    This agent tests the MCPs.
    """
    model_config = ModelConfig()
    mcp_tools = ["sandbox", "generic_operations"]
    agent = BaseAgent(model_name="gpt-5", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="mcp_creation_mcp_configs.json")
    response, metadata = await agent.run(MCP_TEST_PROMPT.format(script=script, ontology=ontology))
    return response


if __name__ == "__main__":
    response = asyncio.run(mcp_test_agent())
    print(response)

    with open('sandbox/code/mcp_creation/0/test_mcp_creation.md', 'w') as file:
        file.write(response)