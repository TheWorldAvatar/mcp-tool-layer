"""ReAct MCP agent. Sessions stay open for one `run()`."""

from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.prompts import load_mcp_prompt
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.prebuilt import create_react_agent

from models.LLMCreator import LLMCreator
from models.MCPConfig import MCPConfig
from models.ModelConfig import ModelConfig
from models.TokenCalculator import TokenCounter
from models.llm_call_telemetry import journal_path, summarize_costs, telemetry_context
from src.extraction_runtime.agent.env import (
    call_required_mcp_tool,
    compose_user_message_with_mcp_instruction,
    exception_details,
    merge_mcp_server_environment,
)
from src.extraction_runtime.agent.loop import (
    best_text_and_meta_from_react_messages,
    project_react_history_to_receipts,
    required_final_call_satisfied,
    stop_repeated_committed_output_calls,
    summarize_react_tool_activity,
)
from src.utils.global_logger import get_logger


class BaseAgent:
    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        remote_model: bool = True,
        model_config: ModelConfig | None = None,
        mcp_set_name: str | None = "mcp_configs.json",
        mcp_tools: list[str] | None = None,
        excluded_tool_names: list[str] | None = None,
        structured_output: bool = False,
        structured_output_schema: Any = None,
    ):
        self.model_name = model_name
        self.remote_model = remote_model
        self.model_config = model_config or ModelConfig()
        self.mcp_config = MCPConfig(config_name=mcp_set_name)
        self.mcp_tools = mcp_tools or []
        self.excluded_tool_names = {
            str(value).strip()
            for value in (excluded_tool_names or [])
            if str(value).strip()
        }
        self.logger = get_logger("agent", "BaseAgent")
        self.llm = LLMCreator(
            model=self.model_name,
            remote_model=self.remote_model,
            model_config=self.model_config,
            structured_output=structured_output,
            structured_output_schema=structured_output_schema,
        ).setup_llm()

    async def run(
        self,
        task_instruction: str,
        recursion_limit: int | None = None,
        required_initial_tool: str | None = None,
        required_initial_tool_args: dict[str, Any] | None = None,
        required_final_tool: str | None = None,
        required_final_tool_args: dict[str, Any] | None = None,
        mcp_instruction_in_user: bool = False,
        task_continuation: str | None = None,
        react_history_projection: bool = True,
        react_argument_firewall: bool = True,
    ) -> tuple[str, dict[str, Any]]:
        preview = (
            task_instruction[:200] + "..."
            if len(task_instruction) > 200
            else task_instruction
        )
        self.logger.info("Starting BaseAgent run with task: %s", preview)
        parent_call_id = uuid4().hex
        server_cfg = self.mcp_config.get_config(self.mcp_tools)
        try:
            merged_cfg: dict[str, Any] = {}
            for name, cfg in (server_cfg or {}).items():
                clean = {key: value for key, value in (cfg or {}).items() if key != "description"}
                clean["env"] = merge_mcp_server_environment(
                    dict(clean.get("env") or {}),
                    dict(os.environ),
                )
                merged_cfg[name] = clean
            server_cfg = merged_cfg
        except Exception:
            pass

        mcp_client = MultiServerMCPClient(server_cfg)
        reply_text = ""
        meta: dict[str, Any] = {}
        required_initial_tool_call: dict[str, Any] | None = None
        required_tool_fallback: dict[str, Any] | None = None

        async with AsyncExitStack() as stack:
            sessions: dict[str, Any] = {}
            for server_name in self.mcp_tools:
                try:
                    session = await stack.enter_async_context(mcp_client.session(server_name))
                    sessions[server_name] = session
                except Exception as exc:
                    raise RuntimeError(
                        f"Could not open MCP session for '{server_name}': {exc}"
                    ) from exc

            tools = []
            tool_sessions: dict[str, Any] = {}
            for server_name, session in sessions.items():
                server_tools = await load_mcp_tools(session)
                server_tools = [
                    tool
                    for tool in server_tools
                    if str(getattr(tool, "name", "") or "").strip()
                    not in self.excluded_tool_names
                ]
                for tool in server_tools:
                    tool_name = str(getattr(tool, "name", "") or "").strip()
                    if tool_name:
                        tool_sessions[tool_name] = session
                tools.extend(server_tools)
                self.logger.info(
                    "Loaded %s MCP tools from %s", len(server_tools), server_name
                )
            if not tools:
                raise RuntimeError("No MCP tools were successfully loaded.")

            required_initial_name = str(required_initial_tool or "").strip()
            if required_initial_name:
                initial_session = tool_sessions.get(required_initial_name)
                if initial_session is None:
                    raise RuntimeError(
                        f"Required initial MCP tool `{required_initial_name}` "
                        "is not exposed by the open sessions"
                    )
                required_initial_tool_call = await call_required_mcp_tool(
                    initial_session,
                    tool_name=required_initial_name,
                    arguments=required_initial_tool_args,
                    phase="initial",
                )

            instruction_msgs = []
            for server_name, session in sessions.items():
                try:
                    instruction_msgs.extend(await load_mcp_prompt(session, "instruction"))
                except Exception as exc:
                    self.logger.warning(
                        "'%s' lacks an 'instruction' prompt (%s)", server_name, exc
                    )

            system_text = ""

            if mcp_instruction_in_user:
                if not instruction_msgs:
                    raise RuntimeError(
                        "MCP user-message instruction delivery requested, but no "
                        "'instruction' prompt was exposed"
                    )
                task_instruction = compose_user_message_with_mcp_instruction(
                    task_instruction=task_instruction,
                    mcp_instruction="\n\n".join(
                        str(message.content) for message in instruction_msgs
                    ),
                    task_continuation=task_continuation or "",
                )
                instruction_msgs = []

            def occurrence_pre_model_hook(state: dict[str, Any]) -> dict[str, Any]:
                return project_react_history_to_receipts(
                    state,
                    classify_argument_owner_mismatch=react_argument_firewall,
                )

            def occurrence_post_model_hook(state: dict[str, Any]) -> dict[str, Any]:
                return stop_repeated_committed_output_calls(
                    state,
                    argument_firewall=react_argument_firewall,
                )

            agent_kwargs: dict[str, Any] = {
                "pre_model_hook": (
                    occurrence_pre_model_hook if react_history_projection else None
                ),
                "post_model_hook": (
                    occurrence_post_model_hook
                    if react_history_projection or react_argument_firewall
                    else None
                ),
            }
            if instruction_msgs:
                system_text = "\n\n".join(message.content for message in instruction_msgs)
                agent = create_react_agent(
                    self.llm,
                    tools,
                    prompt=ChatPromptTemplate.from_messages(
                        [
                            ("system", system_text),
                            MessagesPlaceholder("messages"),
                        ]
                    ),
                    **agent_kwargs,
                )
            else:
                agent = create_react_agent(self.llm, tools, **agent_kwargs)

            invoke_kwargs = {"messages": [HumanMessage(content=task_instruction)]}
            counter = TokenCounter(log_fn=self.logger.info)
            config: dict[str, Any] = {"callbacks": [counter]}
            if recursion_limit is not None:
                config["recursion_limit"] = recursion_limit
            try:
                with telemetry_context(
                    parent_call_id,
                    {"component": "BaseAgent", "agent_model": self.model_name},
                ):
                    result = await agent.ainvoke(invoke_kwargs, config)
            except BaseException as exc:
                leaves = exception_details(exc)
                if leaves:
                    self.logger.error(
                        "Agent failure leaf cause(s): %s",
                        json.dumps(leaves, ensure_ascii=False),
                    )
                raise

            tool_activity = summarize_react_tool_activity(
                result["messages"],
                classify_argument_owner_mismatch=react_argument_firewall,
            )
            if required_initial_tool_call is not None:
                tool_activity["tool_outputs"].insert(0, required_initial_tool_call)
                tool_activity["executed_tool_names"].insert(
                    0, required_initial_tool_call["name"]
                )
                tool_activity["executed_tool_name_set"] = sorted(
                    set(tool_activity["executed_tool_names"])
                )
                tool_activity["tool_message_count"] += 1

            required_name = str(required_final_tool or "").strip()
            if required_name and not required_final_call_satisfied(
                tool_activity,
                tool_name=required_name,
                required_arguments=required_final_tool_args,
            ):
                session = tool_sessions.get(required_name)
                if session is None:
                    raise RuntimeError(
                        f"Required final MCP tool `{required_name}` "
                        "is not exposed by the open sessions"
                    )
                required_tool_fallback = await call_required_mcp_tool(
                    session,
                    tool_name=required_name,
                    arguments=required_final_tool_args,
                    phase="final",
                )
                tool_activity["tool_outputs"].append(required_tool_fallback)
                tool_activity["executed_tool_names"].append(required_name)
                tool_activity["executed_tool_name_set"] = sorted(
                    set(tool_activity["executed_tool_names"])
                )
                tool_activity["tool_message_count"] += 1

            reply_text, meta = best_text_and_meta_from_react_messages(result["messages"])

        aggregated = {
            "prompt_tokens": counter.prompt_tokens,
            "completion_tokens": counter.completion_tokens,
            "total_tokens": counter.total_tokens,
            "calls": counter.calls,
            "estimated_cost_usd": round(
                counter.input_cost_usd + counter.output_cost_usd, 6
            ),
        }
        actual_costs = summarize_costs(journal_path(), parent_call_id)
        metadata = {
            "model_name": meta.get("model_name", ""),
            "final_call_token_usage": meta.get("token_usage", {}),
            "aggregated_usage": aggregated,
            "per_call_usage": counter.calls_detail,
            "tool_activity": tool_activity,
            "required_initial_tool_call": required_initial_tool_call,
            "required_final_tool_fallback": required_tool_fallback,
            "parent_call_id": parent_call_id,
            "actual_cost_usd": actual_costs["actual_cost_usd"],
            "pending_cost_count": actual_costs["pending_calls"],
            "resolved_cost_count": actual_costs["resolved_calls"],
            "billable_llm_calls": actual_costs["billable_calls"],
            "generation_ids": actual_costs["generation_ids"],
            "cost_journal": str(journal_path()),
            "mcp_instruction": system_text,
            "mcp_instruction_in_user": bool(mcp_instruction_in_user),
        }
        return reply_text, metadata
