"""
BaseAgent – a reusable ReAct-based agent that can load one or more MCP
tools and keep their sessions alive for the whole run.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple, Optional, Callable
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from models.LLMCreator import LLMCreator
from models.MCPConfig import MCPConfig
from models.ModelConfig import ModelConfig
from models.TokenCalculator import TokenCounter
from src.utils.global_logger import get_logger

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
        self.logger = get_logger("agent", "BaseAgent")

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
        self.logger.info(f"Starting BaseAgent run with task: {task_instruction}")
        
        # 1️⃣ MCP configs
        server_cfg = self.mcp_config.get_config(self.mcp_tools)
        self.logger.info(f"Loaded MCP tools: {self.mcp_tools}")

        # 2️⃣ Docker check (non-fatal)
        if not await self.mcp_config.is_docker_running():
            self.logger.error("Docker is not running – MCP tools need it.")
            # raise RuntimeError("Docker is not running – MCP tools need it.")

        # 3️⃣ Multi-server MCP client
        client = MultiServerMCPClient(server_cfg)
        self.logger.info("Created MultiServerMCPClient")

        try:
            tools = await client.get_tools()
            self.logger.info(f"Loaded {len(tools)} MCP tools")

            # optional instruction prompts
            instruction_msgs = []
            for server_name in self.mcp_tools:
                try:
                    msgs = await client.get_prompt(server_name, "instruction")
                    instruction_msgs.extend(msgs)
                    self.logger.info(f"Loaded instruction prompt from {server_name}")
                except Exception as e:
                    self.logger.warning(f"'{server_name}' lacks an 'instruction' prompt ({e})")

        except Exception as exc:
            self.logger.error(f"Could not load MCP tools or prompts: {exc}")
            raise RuntimeError(
                f"Could not load MCP tools or prompts: {exc}"
            ) from exc

        if not tools:
            self.logger.error("No MCP tools were successfully loaded.")
            raise RuntimeError("No MCP tools were successfully loaded.")

        # 4️⃣ ReAct agent
        if instruction_msgs:
            system_text = "\n\n".join(m.content for m in instruction_msgs)
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", system_text),
                    MessagesPlaceholder("messages"),
                ]
            )
            agent = create_react_agent(self.llm, tools, prompt=prompt)
            self.logger.info("Created ReAct agent with custom prompt")
        else:
            agent = create_react_agent(self.llm, tools)
            self.logger.info("Created ReAct agent with default prompt")

        # 5️⃣ Run with per-call + aggregated accounting
        invoke_kwargs: Dict[str, Any] = {
            "messages": [HumanMessage(content=task_instruction)]
        }
        self.logger.info("Starting agent execution")

        counter = TokenCounter(log_fn=self.logger.info)
        config: Dict[str, Any] = {"callbacks": [counter]}
        if recursion_limit is not None:
            config["recursion_limit"] = recursion_limit

        result = await agent.ainvoke(invoke_kwargs, config)
        self.logger.info("Agent execution completed")

        # 6️⃣ Final message + final-call meta (optional)
        last_msg = result["messages"][-1]
        meta = getattr(last_msg, "response_metadata", {}) or {}
        final_call_token_usage = meta.get("token_usage", {})  # may be empty depending on provider

        # Aggregated totals
        aggregated = {
            "prompt_tokens": counter.prompt_tokens,
            "completion_tokens": counter.completion_tokens,
            "total_tokens": counter.total_tokens,
            "calls": counter.calls,
            "total_cost_usd": round(counter.input_cost_usd + counter.output_cost_usd, 6),
        }

        # Full metadata payload with both views
        metadata = {
            "model_name": meta.get("model_name", ""),
            "final_call_token_usage": final_call_token_usage,  # last LLM call only
            "aggregated_usage": aggregated,                    # run-level totals
            "per_call_usage": counter.calls_detail,            # list of per-call dicts
        }

        self.logger.info(
            f"Agent tokens (run-level): {aggregated['total_tokens']} "
            f"over {aggregated['calls']} calls"
        )
        return last_msg.content, metadata


# ─────────────────────────── demo ───────────────────────────
if __name__ == "__main__":

    async def _demo() -> None:
        agent = BaseAgent(mcp_tools=["pubchem", "enhanced_websearch"], mcp_set_name="chemistry.json")
        reply, meta = await agent.run(
            """
            Try to find all representations of the chemical species name: H2edb
            """
        )

        print(reply)
        print(meta["aggregated_usage"])
    asyncio.run(_demo())
