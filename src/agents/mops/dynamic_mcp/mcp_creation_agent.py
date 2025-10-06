from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
import asyncio
import os


ontology = ""
with open('data/ontologies/T-Box.ttl', 'r') as file:
    ontology = file.read()
 
iteration_index = 3 # for synthesis steps

MCP_CREATION_FROM_SCRATCH_PROMPT = f"""

Your task is to create and write a python script for step-by-step knowledge graph creation, according to the ontology schema provided.

## MCP tool usage: 

- Use code_output_tool tool to output the code, make sure the code is working. (You don't give me the code, you output the code to the file.)
- Verify the code as well using the sandbox tools. 
- Please note that code_output_tool only support outputting code to the file, you will not be able to modify the code after it is outputted.
You can only rewrite the entire code and output it again. 

## 📋 Task Configuration

- **Meta Task Name**: `mcp_creation`
- **Task Index**: `{iteration_index}`
- **Script Name**: `mcp_creation.py`
- **Environment**: `mcp_creation` conda environment

## Design principle - Function design:

- Keep in mind the agents that uses the function might not have all the context you have, so your function 
should provide as much information as possible.
- One example is to use Literal in the code to represent all the options explicitly. 
- Another strategy is to provide as many atomic functions as possible:
for example, if you create a general function, the agent might not know what specific input to create a specific type of 
entity. However, if you create a specific function for each type of entity, the agent can use the specific function to create the entity.
- Keep in mind the agent will not be able to create specific type of input, e.g., a  an rdflib term object, as a result:
You must ensure both the input and output of the function are in simple data types, including Literal, int, float, bool, etc.
- If any function includes an integer-based order, you should also provide the order as a parameter. Also, provide functions 
to update the order without creating new entities. In the functions including order as input, you should also implement internal 
checking mechanism to remind the agent certain number of order is missing or inconsistent.
- If one class has a set of attributes, the function to create such entity should also include all the attributes as parameters, default value 
to be "N/A". But in the instruction, you should remind the agent to provide all the attributes when creating the entity. Also, there should be 
functions to update the attributes without creating new entities. 
- The function creating entities should have all the class type/subclass type hardcoded in the function. You should not 
provide functions that create an entity just for representing a class type.


## Design principle - Memory management:

- All agents must share one fixed memory file: memory.ttl.

- On startup: if memory.ttl exists, resume from it; otherwise initialize an empty graph.

- Always use a file-based lock (memory.lock) to guard read → modify → write.

- Write is atomic: serialize to a temp file, then replace the original.

- Every add_* or set_* function must open the memory through this locked context.

- Provide functions:

init_memory() → initialize or resume global memory.

inspect_memory() → return summary of current graph.

export_memory(file_name: str) → snapshot the graph to a given .ttl.

Minimal pattern

Example code:
```pythonimport os, tempfile
from contextlib import contextmanager
from filelock import FileLock
from rdflib import Graph

MEM_DIR = "memory"
MEM_TTL = os.path.join(MEM_DIR, "memory.ttl")
MEM_LOCK = os.path.join(MEM_DIR, "memory.lock")
os.makedirs(MEM_DIR, exist_ok=True)

@contextmanager
def locked_graph(timeout: float = 30.0):
    lock = FileLock(MEM_LOCK)
    lock.acquire(timeout=timeout)
    g = Graph()
    if os.path.exists(MEM_TTL):
        g.parse(MEM_TTL, format="turtle")
    try:
        yield g
        fd, tmp = tempfile.mkstemp(dir=MEM_DIR, suffix=".ttl.tmp"); os.close(fd)
        g.serialize(destination=tmp, format="turtle")
        os.replace(tmp, MEM_TTL)
    finally:
        lock.release()

def init_memory() -> str:
    with locked_graph(): pass
    return "memory"

def inspect_memory() -> str:
    with locked_graph() as g:
        ...
        The inspect_memory function must provide **detailed** information about the current graph, especially the specific IRIs of the entities, 
        the established connections, and the attributes of the entities. (All the information so that the agent can know what has been created and what has not been created.)

def export_memory(file_name: str) -> str:
    if not file_name.endswith(".ttl"):
        raise ValueError("Only .ttl allowed")
    with locked_graph() as g:
        g.serialize(destination=file_name, format="turtle")
    return os.path.abspath(file_name)
```

## Design principle - Correct connection rule:

- Never output triples that reference an IRI which is not itself created and typed in the same transaction.
- Every object of an object property must already exist or be created as a minimal stub with rdf:type and rdfs:label.
- Always follow create-then-connect:
- Create the new node with type and label.
- Ensure any referenced nodes also exist (create stubs if needed).
- Add the connecting triple(s).
- entity creation function must always return the IRI of the created entity.


## Design principle - Hierarchical construction:

- The functions you create should follow the "coarse to fine, bigger to smaller" principle.
- For example, the function should guide the agent to create all the cities first, then for each city, create the buildings, then for each building, create the rooms, etc. This is very important. 
- Also provide functions for deleting the entities you created, as well as the connections you created.

## Design principle - File output:

- The functions you create should always output the final output file as a serialized file, only turtle (ttl) file is allowed.
- The export function should return the full path to the output file.

## Design principle - Example usage:

- Always include ```python \n if __name__ == "__main__": \n ``` in the code and put example usage in the main function.
- The example usage should create at least two top level entities and ** include every function you created **. 
- You **MUST** include every function you created in the example usage, otherwise, you fail the task.
- The example usage must output a ttl file at the end and return the full path to the output file.

## Design principle - Function documentation:
- Always include function-wise comments to explain the function and its connection to the ontology. Use the rdfs:comment information from the ontology to provide clear, context-aware guidance in your function comments.
- Also, if the hash is created by script, e.g., a init function, the function should return the hash so that the agent can know which hash to use across the task.
- Explain exactly the role of each function you created, according to the ontology T-Box provided, especially the comments in the T-Box.
- When write entity creation functions, don't use hash in the URI/IRI of the entity, give them meaningful URI/IRIs. 

## Coverage Requirements:

- The functions you created must cover all the classes, subclasses, properties, subclasses of properties, and relationships in the ontology schema provided.
- For every piece of information in the ontology schema, you should create a function to create it.

## Ontology understanding:

- It is critical to read the rdfs:comment annotations, they provide crucial context, guidance, and restrictions for the knowledge graph creation.
- The functions you create should always reflect the context, guidance, and restrictions provided in the rdfs:comment annotations.
 

## IRI minting rule

- Always mint instance IRIs deterministically based on (rdf:type, normalized rdfs:label).

- Normalize the label by:
    1. Unicode NFKC normalize
    2. Lowercase
    3. Replace non-alphanumeric with -
    4. Strip leading/trailing -.

- Before creating a new IRI, search the KG for an existing instance of the same type with the same normalized label. If found, reuse its IRI.
- Only mint a new IRI if no such instance exists.
- Do not append numeric suffixes (-2, -3); IRIs must be stable across runs.

    Example:

    Label: "A&B Scientific"
    Type: ontosyn:Supplier
    Normalized slug: a-b-scientific
    IRI: https://www.theworldavatar.com/kg/OntoSyn/instance/Supplier/a-b-scientific

## Ontology schema:

```turtle
{ontology}
```

"""
 


async def mcp_creation_agent():
    """
    This agent creates MCPs for the knowledge graph.
    """
    model_config = ModelConfig()
    mcp_tools = ["generic_operations", "sandbox"]
    agent = BaseAgent(model_name="gpt-5", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="mcp_creation_mcp_configs.json")
    
    iteration_index = 3 # for synthesis steps
    # response, metadata = await agent.run(MCP_CREATION_AI_PROMPT.format(ontology=ontology))
    response, metadata = await agent.run(MCP_CREATION_FROM_SCRATCH_PROMPT.format(ontology=ontology, iteration_index=iteration_index), recursion_limit=300)
    # response, metadata = await agent.run(SANDBOX_PROMPT)
    return response

if __name__ == "__main__":
    response = asyncio.run(mcp_creation_agent())
    print(response)