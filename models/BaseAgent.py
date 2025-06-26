"""
BaseAgent – a reusable ReAct-based agent that can load one or more MCP
tools and keeps their sessions open for the entire conversation.

Key changes
-----------
1. Uses **MultiServerMCPClient** so MCP sessions stay alive
   while the agent is running.
2. Closes all MCP sessions in a `finally` block.
3. Supports an optional `recursion_limit` just like before.
"""

from typing import Any, List, Dict, Tuple, Union
import asyncio
import openai

from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from models.LLMCreator import LLMCreator
from models.MCPConfig import MCPConfig
from models.ModelConfig import ModelConfig


class BaseAgent:
    # ───────────────────────────── init ──────────────────────────────
    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        remote_model: bool = True,
        model_config: ModelConfig | None = None,
        mcp_tools: list[str] | None = None,
        structured_output: bool = False,
        structured_output_schema: Any = None,
    ):
        self.model_name = model_name
        self.remote_model = remote_model
        self.model_config = model_config or ModelConfig()
        self.mcp_config = MCPConfig()
        self.mcp_tools = mcp_tools or ["github", "filesystem"]

        # Create the LLM up front
        self.llm = LLMCreator(
            model=self.model_name,
            remote_model=self.remote_model,
            model_config=self.model_config,
            structured_output=structured_output,
            structured_output_schema=structured_output_schema,
        ).setup_llm()

    # ───────────────────────────── run ───────────────────────────────
    async def run(
        self,
        task_instruction: str,
        recursion_limit: int | None = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Execute *task_instruction* with a ReAct agent that can call MCP tools.

        Returns
        -------
        tuple (reply, metadata)
        """
        # 1️⃣  Build server configs (your MCPConfig already knows how)
        server_cfg = self.mcp_config.get_config(self.mcp_tools)

        # 2️⃣  Sanity-check Docker (for the docker MCP tool)
        if not await self.mcp_config.is_docker_running():
            raise RuntimeError("Docker is not running – MCP tools need it.")

        # 3️⃣  Create a **single** MCP client that manages every session
        client = MultiServerMCPClient(server_cfg)

        try:
            tools = await client.get_tools()           # spawn / connect servers
        except Exception as exc:
            raise RuntimeError(f"Could not load MCP tools: {exc}") from exc

        if not tools:
            raise RuntimeError("No MCP tools were successfully loaded.")

        # 4️⃣  Build the ReAct agent
        agent = create_react_agent(self.llm, tools)

        # 5️⃣  Invoke the agent
        invoke_kwargs: Dict[str, Any] = {"messages": task_instruction}
        if recursion_limit is not None:
            result = await agent.ainvoke(
                invoke_kwargs, {"recursion_limit": recursion_limit}
            )
        else:
            result = await agent.ainvoke(invoke_kwargs)

        # 6️⃣  Collect metadata from the last message
        last_msg = result["messages"][-1]
        meta = last_msg.response_metadata
        token_use = meta.get("token_usage", {})

        metadata = {
            "model_name": meta.get("model_name", ""),
            "token_usage": token_use,
            "completion_tokens": token_use.get("completion_tokens", 0),
            "prompt_tokens": token_use.get("prompt_tokens", 0),
            "total_tokens": token_use.get("total_tokens", 0),
        }

        return last_msg.content, metadata

        # 7️⃣  Always close MCP sessions
        # finally:
        #     await client.aclose()


# ───────────────────────────── demo ────────────────────────────────
if __name__ == "__main__":
    async def _demo() -> None:
        agent = BaseAgent(mcp_tools=["agent"])
        reply, meta = await agent.run("Create a txt file named 'hello-from-demo.txt' in the data directory. Also create a docker container named 'demo_container'")
        print(reply)
        print(meta)

    asyncio.run(_demo())
