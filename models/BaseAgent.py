"""
BaseAgent – a reusable ReAct-based agent that can load one or more MCP
tools and keep their sessions alive for the whole run.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from models.LLMCreator import LLMCreator
from models.MCPConfig import MCPConfig
from models.ModelConfig import ModelConfig


class BaseAgent:
    # ──────────────────────────── init ────────────────────────────
    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        remote_model: bool = True,
        model_config: ModelConfig | None = None,
        mcp_set_name: str | None = "mcp_configs.json",
        mcp_tools: List[str] | None = None,
        structured_output: bool = False,
        structured_output_schema: Any = None,
    ):
        self.model_name = model_name
        self.remote_model = remote_model
        self.model_config = model_config or ModelConfig()
        self.mcp_config = MCPConfig(config_name=mcp_set_name)
        self.mcp_tools = mcp_tools or ["github", "filesystem"]

        self.llm = LLMCreator(
            model=self.model_name,
            remote_model=self.remote_model,
            model_config=self.model_config,
            structured_output=structured_output,
            structured_output_schema=structured_output_schema,
        ).setup_llm()

    # ──────────────────────────── run ────────────────────────────
    async def run(
        self,
        task_instruction: str,
        recursion_limit: int | None = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Execute *task_instruction* through a ReAct agent wired to MCP tools."""
        # 1️⃣  Build server configs
        server_cfg = self.mcp_config.get_config(self.mcp_tools)

        # 2️⃣  Ensure Docker (for docker-backed tools)
        if not await self.mcp_config.is_docker_running():
            raise RuntimeError("Docker is not running – MCP tools need it.")

        # 3️⃣  Multi-server MCP client
        client = MultiServerMCPClient(server_cfg)

        try:
            tools = await client.get_tools()

            # gather optional `instruction` prompts
            instruction_msgs = []
            for server_name in self.mcp_tools:
                try:
                    msgs = await client.get_prompt(server_name, "instruction")
                    instruction_msgs.extend(msgs)
                except Exception as e:
                    print(
                        f"[⚠️ Warning] '{server_name}' lacks an "
                        f"'instruction' prompt ({e})"
                    )

        except Exception as exc:
            raise RuntimeError(
                f"Could not load MCP tools or prompts: {exc}"
            ) from exc

        if not tools:
            raise RuntimeError("No MCP tools were successfully loaded.")

        # 4️⃣  Build the ReAct agent
        if instruction_msgs:
            system_text = "\n\n".join(m.content for m in instruction_msgs)
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", system_text),
                    MessagesPlaceholder("messages"),
                ]
            )
            agent = create_react_agent(self.llm, tools, prompt=prompt)
        else:
            agent = create_react_agent(self.llm, tools)

        # 5️⃣  Run the agent
        invoke_kwargs: Dict[str, Any] = {
            "messages": [HumanMessage(content=task_instruction)]
        }
        if recursion_limit is not None:
            result = await agent.ainvoke(
                invoke_kwargs, {"recursion_limit": recursion_limit}
            )
        else:
            result = await agent.ainvoke(invoke_kwargs)

        # 6️⃣  Gather metadata from the final AIMessage
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


# ─────────────────────────── demo ───────────────────────────
if __name__ == "__main__":

    async def _demo() -> None:
        agent = BaseAgent(mcp_tools=["generic", "stack", "task", "sandbox"])
        reply, meta = await agent.run(
            "Create a txt file named 'hello-from-demo.txt' in the data "
            "directory. Also create a docker container named "
            "'demo_container'."
        )
        print("\n--- AGENT REPLY ---")
        print(reply)
        print("\n--- METADATA ---")
        print(meta)

    asyncio.run(_demo())
