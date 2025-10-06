# agent_template.py

import sys, os, logging
from typing import Dict, Optional, List, Tuple
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from dotenv import load_dotenv
from models.MCPConfigDynamic import create_client
from models.MCPConfig import MCPConfig
import asyncio

# ✅ Load variables from `.env` into os.environ
load_dotenv(override=True)
 

async def build_react_agent(
    mcp_keys: Optional[List[str]] = None,
    model_name: str = "gpt-4o-mini",
    base_url: str = os.getenv("REMOTE_BASE_URL"),
    api_key: str = os.getenv("REMOTE_API_KEY"),
    mcp_set_name: str = "mcp_configs.json",
    use_dynamic_config: bool = False,
) -> Tuple[MultiServerMCPClient, object]:
    """
    Build a ReAct agent with MCP tools.
    
    Args:
        mcp_keys: List of MCP tool keys to load
        model_name: LLM model name to use
        base_url: Base URL for the LLM API
        api_key: API key for the LLM
        mcp_set_name: Name of the MCP config file to use (e.g., "mcp_configs.json", "paper_mcp.json")
        use_dynamic_config: If True, use MCPConfigDynamic instead of MCPConfig
    """
    if not base_url or not api_key:
        raise RuntimeError("Missing REMOTE_BASE_URL or REMOTE_API_KEY in .env or environment")
    
    # Choose between dynamic config (hardcoded) or file-based config
    if use_dynamic_config:
        client = create_client(mcp_keys)
    else:
        # Use file-based MCP config like BaseAgent
        mcp_config = MCPConfig(config_name=mcp_set_name)
        server_cfg = mcp_config.get_config(mcp_keys or ["all"])
        client = MultiServerMCPClient(server_cfg)
    
    tools = await client.get_tools()
    if model_name in ["o4-mini", "o1-mini", "gpt-5"]:
        llm = ChatOpenAI(model_name=model_name, base_url=base_url, api_key=api_key)
    else:
        llm = ChatOpenAI(model_name=model_name, temperature=0, base_url=base_url, api_key=api_key)
    agent = create_react_agent(llm, tools)
    return client, agent


async def main():
    # Example 1: Using dynamic config (old behavior)
    client, agent = await build_react_agent(mcp_keys=["docker"], use_dynamic_config=True)
    
    # Example 2: Using file-based config (new behavior)
    # client, agent = await build_react_agent(mcp_keys=["docker"], mcp_set_name="mcp_configs.json")
    
    # Example 3: Using a specific config file
    # client, agent = await build_react_agent(mcp_keys=["generic", "sparql"], mcp_set_name="mops_mcp.json")
    
    INSTRUCTION_PROMPT = """
    Tell me are there any existing docker container in the current machine suitable for running a python script (python 3.11)?  

    You should use list_registered_docker_containers_for_task and register_docker_container tools to find or register the suitable docker container.

    Stick to one container for the whole task.
    
    Then execute some command to show me the system version.
    Your meta_task_name is "sub_base_agent_2".
 
    """
    response = await agent.ainvoke({"messages": INSTRUCTION_PROMPT})
    print(response)

if __name__ == "__main__":
    asyncio.run(main())
    