"""
BaseAgent – a reusable ReAct-based agent that can load one or more MCP
tools and keep their sessions alive for the whole run.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Tuple, Optional, Callable
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.prompts import load_mcp_prompt
from langchain_mcp_adapters.tools import load_mcp_tools

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
        # Truncate task instruction for logging to avoid console spam
        task_preview = task_instruction[:200] + "..." if len(task_instruction) > 200 else task_instruction
        self.logger.info(f"Starting BaseAgent run with task: {task_preview}")
        
        # 1️⃣ MCP configs
        server_cfg = self.mcp_config.get_config(self.mcp_tools)
        self.logger.info(f"Loaded MCP tools: {self.mcp_tools}")
        
        # Compatibility: some MCP client stacks (depending on installed `mcp` version) do not accept a
        # `description` kwarg when creating stdio sessions. Our config files sometimes include it
        # (e.g., `configs/mops_mcp.json` for the `document` server). Strip it to avoid runtime errors.
        try:
            server_cfg = {
                name: {k: v for k, v in (cfg or {}).items() if k != "description"}
                for name, cfg in (server_cfg or {}).items()
            }
        except Exception:
            # If sanitization fails for any reason, fall back to the raw config.
            pass

        # 2️⃣ Docker check (non-fatal)
        if not await self.mcp_config.is_docker_running():
            self.logger.error("Docker is not running – MCP tools need it.")
            # raise RuntimeError("Docker is not running – MCP tools need it.")

        # 3️⃣ Multi-server MCP client
        #
        # IMPORTANT: langchain-mcp-adapters 0.1.0 does NOT allow `async with MultiServerMCPClient(...)`.
        # To ensure deterministic cleanup and avoid spawning a new stdio server process on every tool call,
        # we open one session per configured MCP server for the duration of this run.
        mcp_client = MultiServerMCPClient(server_cfg)

        async with AsyncExitStack() as stack:
            sessions: Dict[str, Any] = {}
            for server_name in self.mcp_tools:
                try:
                    session = await stack.enter_async_context(mcp_client.session(server_name))
                    sessions[server_name] = session
                except Exception as exc:
                    self.logger.error(f"Could not open MCP session for '{server_name}': {exc}")
                    raise RuntimeError(f"Could not open MCP session for '{server_name}': {exc}") from exc

            # Load tools bound to the open sessions (so tool calls reuse the session)
            tools = []
            for server_name, session in sessions.items():
                try:
                    server_tools = await load_mcp_tools(session)
                    tools.extend(server_tools)
                    self.logger.info(f"Loaded {len(server_tools)} MCP tools from {server_name}")
                except Exception as exc:
                    self.logger.error(f"Could not load MCP tools from '{server_name}': {exc}")
                    raise RuntimeError(f"Could not load MCP tools from '{server_name}': {exc}") from exc

            if not tools:
                self.logger.error("No MCP tools were successfully loaded.")
                raise RuntimeError("No MCP tools were successfully loaded.")

            # optional instruction prompts (fetch every time as they may change)
            instruction_msgs = []
            for server_name, session in sessions.items():
                try:
                    msgs = await load_mcp_prompt(session, "instruction")
                    instruction_msgs.extend(msgs)
                    self.logger.info(f"Loaded instruction prompt from {server_name}")
                except Exception as e:
                    self.logger.warning(f"'{server_name}' lacks an 'instruction' prompt ({e})")

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

            try:
                result = await agent.ainvoke(invoke_kwargs, config)
            except BaseException as e:
                # Python 3.11+: langgraph can raise ExceptionGroup/TaskGroup errors.
                # Surface the nested exceptions so pipeline logs are actionable.
                sub_excs = getattr(e, "exceptions", None)
                if sub_excs:
                    try:
                        self.logger.error(f"Agent raised an exception group with {len(sub_excs)} sub-exception(s):")
                        for i, sub in enumerate(sub_excs, start=1):
                            self.logger.error(f"  [{i}] {type(sub).__name__}: {sub}")
                    except Exception:
                        pass
                raise
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
