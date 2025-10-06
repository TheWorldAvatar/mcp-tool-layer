from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
import asyncio
import os
import glob


ontology = "ontomops"

def load_sampling_report():
    """Load the latest ontology sampling report from playground directory."""
    sampling_files = glob.glob("playground/ontomops_sampling_*.md")
    if not sampling_files:
        return "No sampling report found."
    
    # Get the most recent file
    latest_file = max(sampling_files, key=os.path.getmtime)
    
    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading sampling report: {e}"

QUERY_PROBING_PROMPT = """

You are a query probing agent tasked with creating comprehensive SPARQL query examples based on a real ontology knowledge graph. 

You have access to an ontology sampling report that contains:
- Real sampled instances from each class in the T-Box
- 2-hop subgraphs around these instances showing actual relationships
- Namespace declarations and URI patterns
- Connection patterns between entities

Your task is to analyze these samples and create diverse SPARQL query examples that work with the actual data.

## Sampling Report Data

{sampling_report}

## Your Objectives

**Primary Goal**: Create SPARQL query examples based on the sampled instances and relationships shown above.

You should:

- **Analyze the samples**: Look at the actual instances, properties, and connection patterns in the subgraphs
- **Create diverse queries**: Cover simple to complex patterns based on what you observe in the data
- **Use real URIs**: Base your queries on the actual instance URIs and property relationships shown
- **Test extensively**: Verify each query works and returns meaningful results
- **Record systematically**: Use MCP functions to save validated queries with descriptions

## Query Categories to Cover

1. **Instance-based queries**: Using specific URIs from the samples
2. **Class-based queries**: Finding instances of specific classes  
3. **Property-based queries**: Exploring relationships shown in subgraphs
4. **Multi-hop queries**: Following connection chains from the 2-hop data
5. **Aggregation queries**: Counting, grouping based on patterns observed
6. **Complex joins**: Combining multiple classes/properties from the samples

## MCP Functions Available

- `list_sparql_example_names`: Check existing query warehouse
- `retrieve_sparql_example`: Get existing query details  
- `insert_sparql_example`: Save new validated queries with descriptions and sample results

## Guidelines

**Critical**: Use only "probe" mode when testing queries - never "full" mode
**Critical**: Include proper namespace prefixes in all queries (use the ones from the report)
**Critical**: Only save queries that are confirmed valid and return useful results
**Critical**: Base queries on actual data patterns from the sampling report above
**Critical**: Include sample results when saving queries to the warehouse

Start by examining the namespace declarations and sampled instances, then build queries that explore the relationships and patterns you observe in the actual data.

"""


async def query_probing_agent(ontology: str):
    logger = get_logger("agent", "QueryProbingAgent")
    logger.info(f"Starting query probing agent for ontology: {ontology}")
    
    # Load the sampling report
    sampling_report = load_sampling_report()
    logger.info(f"Loaded sampling report: {len(sampling_report)} characters")
    
    # MCP tools available for SPARQL operations
    mcp_tools = ["query_sparql"]
    model_config = ModelConfig(temperature=0.4, top_p=0.6)
    agent = BaseAgent(model_name="gpt-4.1", 
                     model_config=model_config, 
                     remote_model=True, 
                     mcp_tools=mcp_tools, 
                     mcp_set_name="sparql.json")
    
    # Format prompt with sampling report data
    formatted_prompt = QUERY_PROBING_PROMPT.format(sampling_report=sampling_report)
    
    response, metadata = await agent.run(formatted_prompt, recursion_limit=600)
    
    logger.info("Query probing completed")
    print(response)




if __name__ == "__main__":
    # Reduced iterations since we now have rich sampling data to work with
    for i in range(3):
        ontology_list = ["ontomops"]
        for ontology in ontology_list:
            print(f"\n=== Query Probing Session {i+1} for {ontology} ===")
            asyncio.run(query_probing_agent(ontology))

    