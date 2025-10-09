"""Agent utilities module for dynamic MCP agent."""
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig


def create_agent(tools=None, model="gpt-4o", temp=0.2, top_p=0.2, mcp_set="run_created_mcp.json"):
    """
    Create a BaseAgent instance with specified configuration.
    
    Args:
        tools: List of MCP tools to use
        model: Model name
        temp: Temperature setting
        top_p: Top-p sampling parameter
        mcp_set: MCP configuration set name
        
    Returns:
        Configured BaseAgent instance
    """
    return BaseAgent(
        model_name=model,
        model_config=ModelConfig(temperature=temp, top_p=top_p),
        remote_model=True,
        mcp_tools=tools or [],
        mcp_set_name=mcp_set,
    )


async def run_llm(prompt: str, recursion=600, tools=("llm_created_mcp", "enhanced_websearch")):
    """
    Run LLM with given prompt and tools.
    
    Args:
        prompt: The prompt to send to the LLM
        recursion: Recursion limit for the agent
        tools: Tuple of tool names to use
        
    Returns:
        Tuple of (response, metadata)
    """
    agent = create_agent(tools=list(tools))
    resp, meta = await agent.run(prompt, recursion_limit=recursion)
    return resp, meta

