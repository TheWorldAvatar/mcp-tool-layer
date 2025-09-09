from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
import asyncio


ontology = ""
with open('data/ontologies/output.ttl', 'r') as file:
    ontology = file.read()
 
 
MCP_CREATION_PROMPT = f"""

You are in charge of one of the step in a bigger project. 

The bigger project aims to extract information from academic articles and build a knowledge graph.

Your specific task is to create functions that allows the agent to build up the knowledge graph step by step. 

For example, if the task is to create Knowledge Graph for buildings, you need to create the following functions:

1. create_city(city_name)
2. add_building(building_id, city_id, building_type: Literal[building_type_1, building_type_2, ...])
3. add_floor(floor_id, building_id)
4. add_room(room_id, floor_id)
... 

The purpose is to allow following agents to build up the knowledge graph from coarse to fine, bigger to smaller, and allow 
retries and corrections. Keep in mind the following agents using the functions you created, will not have access to the ontology, so you need to include all the information in the functions you created.

Task name to be "mcp_creation". task_index to be 0. script_name to be "mcp_creation.py".

Use code_output_tool tool to output the code, verify the code as well using the sandbox tools. Adjust the script until you have the code working.

Use the mcp_creation conda environment to run the script and install packages if needed.

Here are some general guidelines:

 - **Critical:**: The functions will need to be used by a MCP server later, so you need to consider that the MCP server might init the object under the class you created multiple times. 
As a result, file-based object memory is critical, other wise when in use, the object will be init multiple times, and the memory will be lost. 

The memory object will need a hash-based file-name, where given the hash, each function created can resume creation and update of the object. 

 - **Critical:** When using hash-based file-name memory mechanism, you **MUST** implement locking mechanism to avoid race conditions.
 - **Critical:** Make sure the code you created fully represent **ALL** information in the ontology provided, including the class hierarchy, subclasses, properties, subclasses of properties, and relationships. 
 - **Critical:** If the ontology suggest a finete set of options, you should use Literal in the code to represent all the options. 
 - **Important:** Always give hash-based IRI/URI to the entities you create, give then labels as well. Hash should use timestamp as one of the input to make sure it is unique.
 - **Important:**: Provide function-wise comments to explain the function and its connection to the ontology. Also, if the hash is created by script, e.g., a init function, the function should return the hash so that the agent can know which hash to use across the task.
 - Make sure the functions you created cover all classes, subclasses, properties, subclasses of properties, and relationships in the ontology provided. 
 - Always create individual functions for subclasses and so on. For example, in the building example, there might be many different types of buildings, so you should create one function for each type of building.
 - Make sure you breakdown the functions into atomic operations, so that the following agents can use them to build up the knowledge graph step by step.
 - Include ```python \n if __name__ == "__main__": \n ``` in the code and put example usage in the main function.
 - In your final response, clearly indicate the meta_task_name, task_index, and script_name. The status of the code execution. The final response should include nothing else. 
 - Always have a function to output the final output as a serialized file. 


Here is the ontology: {ontology} 
"""


SANDBOX_PROMPT = f"""
Your task is to run a python script in the sandbox.

task_name: mcp_creation
iteration_number: 0
script_name: test.py

use conda environment name: mcp_creation

Give me the output. 
"""



async def mcp_creation_agent():
    """
    This agent creates MCPs for the knowledge graph.
    """
    model_config = ModelConfig()
    mcp_tools = ["generic_operations", "sandbox"]
    agent = BaseAgent(model_name="gpt-5", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="mcp_creation_mcp_configs.json")
    
    # response, metadata = await agent.run(MCP_CREATION_AI_PROMPT.format(ontology=ontology))
    response, metadata = await agent.run(MCP_CREATION_PROMPT.format(ontology=ontology))
    # response, metadata = await agent.run(SANDBOX_PROMPT)
    return response

if __name__ == "__main__":
    response = asyncio.run(mcp_creation_agent())
    print(response)