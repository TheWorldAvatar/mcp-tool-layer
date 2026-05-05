"""
BaseAgent – a reusable ReAct-based agent that can load one or more MCP
tools and keep their sessions alive for the whole run.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Tuple, Optional, Callable
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
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


def _normalize_ai_message_content(content: Any) -> str:
    """
    Turn LangChain AIMessage.content into a plain string.

    ReAct runs may end with ``content=[]`` (tool-only / multimodal blocks with no text).
    Calling ``str([])`` would yield ``\"[]\"`` (2 chars) and break downstream checks — avoid that.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text" and "text" in content:
            return str(content["text"])
        if "text" in content:
            return str(content["text"])
        return ""
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(str(block["text"]))
                elif "text" in block:
                    parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        return "".join(parts)
    return str(content)


def _is_trivial_agent_reply(text: str) -> bool:
    """
    True if the model reply is an empty / placeholder token we should not treat as final output.

    ReAct often ends with ``{}`` / ``[]`` or ``str([])``-style noise; the real JSON may appear
    on an earlier AIMessage turn.
    """
    t = (text or "").strip()
    if not t:
        return True
    if t in ("{}", "[]"):
        return True
    if len(t) <= 3 and t.lower().rstrip(".") in ("ok", "yes", "no"):
        return True
    return False


def _best_text_and_meta_from_react_messages(messages: List[Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Prefer the **last** AIMessage whose normalized text is substantive (non-trivial).

    If the graph ends with an empty AIMessage (``content=[]``) or a placeholder ``{}``/``[]``,
    walk backwards so we still return the model's last useful reply.
    """
    if not messages:
        return "", {}

    ai_messages: List[AIMessage] = [m for m in messages if isinstance(m, AIMessage)]
    if not ai_messages:
        last = messages[-1]
        meta = getattr(last, "response_metadata", {}) or {}
        return _normalize_ai_message_content(getattr(last, "content", None)), meta

    for msg in reversed(ai_messages):
        text = _normalize_ai_message_content(msg.content)
        if text.strip() and not _is_trivial_agent_reply(text):
            meta = getattr(msg, "response_metadata", {}) or {}
            return text, meta

    last_ai = ai_messages[-1]
    meta = getattr(last_ai, "response_metadata", {}) or {}
    return _normalize_ai_message_content(last_ai.content), meta


def _summarize_react_tool_activity(messages: List[Any]) -> Dict[str, Any]:
    """
    Summarize actual tool activity observed in a ReAct run.

    This inspects both:
    - `AIMessage.tool_calls` planned by the model
    - `ToolMessage` objects emitted after tool execution
    """
    ai_messages: List[AIMessage] = [m for m in messages if isinstance(m, AIMessage)]
    tool_messages: List[ToolMessage] = [m for m in messages if isinstance(m, ToolMessage)]

    tool_call_names: List[str] = []
    for msg in ai_messages:
        for call in getattr(msg, "tool_calls", []) or []:
            name = str((call or {}).get("name") or "").strip()
            if name:
                tool_call_names.append(name)

    executed_tool_names: List[str] = []
    for msg in tool_messages:
        name = str(getattr(msg, "name", "") or "").strip()
        if name:
            executed_tool_names.append(name)

    return {
        "ai_message_count": len(ai_messages),
        "tool_message_count": len(tool_messages),
        "planned_tool_call_count": len(tool_call_names),
        "planned_tool_names": tool_call_names,
        "executed_tool_names": executed_tool_names,
        "executed_tool_name_set": sorted(set(executed_tool_names)),
    }


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
        #
        # Many MCP servers in this repo are pure local stdio Python servers and do NOT require Docker.
        # Some optional external MCP servers do. Keep this as a warning to avoid confusion/noise.
        if not await self.mcp_config.is_docker_running():
            self.logger.warning(
                "Docker is not running (this is OK for local stdio MCP servers; "
                "only Docker-based MCP servers require it)."
            )

        # 3️⃣ Multi-server MCP client
        #
        # IMPORTANT: langchain-mcp-adapters 0.1.0 does NOT allow `async with MultiServerMCPClient(...)`.
        # To ensure deterministic cleanup and avoid spawning a new stdio server process on every tool call,
        # we open one session per configured MCP server for the duration of this run.
        mcp_client = MultiServerMCPClient(server_cfg)

        reply_text = ""
        meta: Dict[str, Any] = {}
        final_call_token_usage: Dict[str, Any] = {}

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

            tool_activity = _summarize_react_tool_activity(result["messages"])
            self.logger.info(
                "ReAct tool activity: planned_tool_calls=%s, tool_messages=%s, executed_tools=%s",
                tool_activity["planned_tool_call_count"],
                tool_activity["tool_message_count"],
                tool_activity["executed_tool_name_set"],
            )

            # 6️⃣ Final substantive AI reply + meta (last AIMessage may be empty: content=[])
            reply_text, meta = _best_text_and_meta_from_react_messages(result["messages"])
            if not reply_text.strip():
                _aim = [m for m in result["messages"] if isinstance(m, AIMessage)]
                _last_len = (
                    len(_normalize_ai_message_content(_aim[-1].content)) if _aim else 0
                )
                self.logger.warning(
                    "ReAct run ended with no non-empty AIMessage text; last normalized AIMessage length=%s.",
                    _last_len,
                )
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
            "tool_activity": tool_activity,
        }

        self.logger.info(
            f"Agent tokens (run-level): {aggregated['total_tokens']} "
            f"over {aggregated['calls']} calls"
        )
        return reply_text, metadata


# ─────────────────────────── demo ───────────────────────────
if __name__ == "__main__":

    def _self_test_best_text() -> None:
        """Reproduce str([])->\"[]\" failure: last AIMessage empty, earlier has body."""
        from langchain_core.messages import ToolMessage

        long_body = "x" * 80
        msgs = [
            HumanMessage(content="task"),
            AIMessage(content=long_body),
            ToolMessage(content="{}", tool_call_id="a"),
            AIMessage(content=[]),
        ]
        text, _meta = _best_text_and_meta_from_react_messages(msgs)
        assert text == long_body, (text, len(text))
        assert str([]) == "[]" and len("[]") == 2  # document the old pitfall

        json_prev = '{"entities": [' + '"x",' * 40 + '"z"]}'
        msgs2 = [
            HumanMessage(content="task"),
            AIMessage(content=json_prev),
            AIMessage(content="{}"),
        ]
        text2, _ = _best_text_and_meta_from_react_messages(msgs2)
        assert text2 == json_prev, text2

    _self_test_best_text()
    print("BaseAgent: _best_text self-test OK")

    async def _demo() -> None:
        agent = BaseAgent(mcp_tools=["pubchem", "enhanced_websearch"], mcp_set_name="chemistry.json")
        reply, meta = await agent.run(
            """
            Try to find all representations of the chemical species name: H2edb
            """
        )

        print(reply)
        print(meta["aggregated_usage"])

    # Full MCP demo is opt-in (needs servers / keys).
    import os

    if os.environ.get("BASE_AGENT_RUN_MCP_DEMO") == "1":
        asyncio.run(_demo())
