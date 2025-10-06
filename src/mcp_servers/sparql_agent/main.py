from fastmcp import FastMCP
from src.utils.global_logger import mcp_tool_logger
from models.SubBaseAgent import build_react_agent

 

QUERY_AGENT_DESCRIPTION = """
This tool is backened by a query agent that can perform complex SPARQL queries against knowledge graph endpoints.

To use this tool, you need to provide a detailed prompt stating what data you want to retrieve 
and provide detailed context of your task. 
""" 


mcp = FastMCP(name="sparql_agent")
 
@mcp.tool(name="query_sparql_agent", description=QUERY_AGENT_DESCRIPTION)
@mcp_tool_logger
async def query_sparql_with_agent(query_prompt: str):

    QUERY_PROMPT_TEMPLATE = f"""
    You should complete what the query prompt is asking for.

    You must spare no effort to try different SPARQL queries until you get the desired results.

    Be patient, the SPARQL queries might take a while to execute.

    Make sure you include namespace prefixes in the SPARQL queries.

    Take look at the T-Box information to understand the schema of the knowledge graph.

    Also, you should use list_sparql_example_names, retrieve_sparql_example, etc to help you to get SPARQL query examples that have been verified.  

    The following is the query prompt: \n

    {query_prompt}
    """


    _, agent = await build_react_agent(
        model_name="gpt-4o-mini",
        mcp_keys=["query_sparql"], 
        mcp_set_name= "sparql.json",
        use_dynamic_config=False
    )
    result = await agent.ainvoke({"messages": QUERY_PROMPT_TEMPLATE}, {"recursion_limit": 600})
    response = result["messages"][-1].content
    return response
 
if __name__ == "__main__":
    mcp.run(transport="stdio")