# agent_template.py

import sys, os, logging
from typing import Dict, Optional, List, Tuple
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from dotenv import load_dotenv
from models.MCPConfigDynamic import create_client
import asyncio

# ✅ Load variables from `.env` into os.environ
load_dotenv(override=True)
 
def setup_logging(name: str = __name__, log_file: str = "agent.log") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler(sys.stderr))
        logger.addHandler(logging.FileHandler(log_file, encoding="utf-8"))
    return logger


async def build_react_agent(
    mcp_keys: Optional[List[str]] = None,
    model_name: str = "gpt-4o-mini",
    base_url: str = os.getenv("REMOTE_BASE_URL"),
    api_key: str = os.getenv("REMOTE_API_KEY"),
) -> Tuple[MultiServerMCPClient, object]:
    if not base_url or not api_key:
        raise RuntimeError("Missing REMOTE_BASE_URL or REMOTE_API_KEY in .env or environment")
    client = create_client(mcp_keys)
    tools = await client.get_tools()
    llm = ChatOpenAI(model_name=model_name, temperature=0, base_url=base_url, api_key=api_key)
    agent = create_react_agent(llm, tools)
    return client, agent


async def main():
    client, agent = await build_react_agent(mcp_keys=["generic", "sandbox", "stack_operations", "task_operations"])
    INSTRUCTION_PROMPT = """
    Say hello to the world. 
    
    Output a python script that prints "hello world", use output_code tool to output the code (This is the only tool you can use to output the code)

    meta_task_name: hello_world. 
    """
    response = await agent.ainvoke({"messages": INSTRUCTION_PROMPT})
    print(response["messages"][-1].content)

if __name__ == "__main__":
    asyncio.run(main())
    