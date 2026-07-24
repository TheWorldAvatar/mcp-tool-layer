"""KgqaAgent — multi-domain ReAct wrapper over BaseAgent."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from mini_marie.kgqa.mcp_router import RouteResult
from mini_marie.kgqa.question_catalog import CatalogEntry
from src.utils.global_logger import get_logger


class KgqaAgent:
    """ReAct agent for multi-KG Q&A with dynamic MCP loading."""

    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        remote_model: bool = True,
        model_config: Optional[ModelConfig] = None,
    ):
        self.logger = get_logger("agent", "KgqaAgent")
        self.model_name = model_name
        self.remote_model = remote_model
        self.model_config = model_config
        from mini_marie.cache_paths import configs_dir

        self.config_path = configs_dir() / "mini_marie_mcps.json"

    async def ask(
        self,
        question: str,
        *,
        route: RouteResult,
        recursion_limit: int = 200,
    ) -> Tuple[str, Dict[str, Any]]:
        enhanced = self._enhance_question(question, route)
        base_agent = BaseAgent(
            model_name=self.model_name,
            remote_model=self.remote_model,
            model_config=self.model_config,
            mcp_set_name=str(self.config_path),
            mcp_tools=route.mcp_servers,
            structured_output=False,
        )
        result, metadata = await base_agent.run(
            task_instruction=enhanced,
            recursion_limit=recursion_limit,
        )
        metadata["mcp_servers"] = route.mcp_servers
        metadata["route_reason"] = route.reason
        metadata["route_domain"] = route.domain
        if route.catalog_entry:
            metadata["catalog_entry_id"] = route.catalog_entry.id
            metadata["workflow_id"] = route.catalog_entry.workflow_id
        return result, metadata

    def _enhance_question(self, question: str, route: RouteResult) -> str:
        entry: Optional[CatalogEntry] = route.catalog_entry
        workflow_hint = ""
        if entry and entry.workflow_id:
            if entry.domain == "mof":
                workflow_hint = (
                    f"\nCatalog match: workflow_id `{entry.workflow_id}`. "
                    "You MUST call `run_competency_online` with this exact workflow_id first. "
                    "Do not substitute atomic tools for catalog competency questions."
                )
            elif entry.domain == "chemistry":
                workflow_hint = (
                    f"\nCatalog match: workflow_id `{entry.workflow_id}`. "
                    "You MUST call `run_competency_online` with this exact workflow_id. "
                    "Do not invent other workflow ids or substitute atomic-only tool chains."
                )
            elif entry.domain == "city":
                workflow_hint = (
                    f"\nCatalog match: workflow `{entry.workflow_id}`. "
                    "You MUST call `run_workflow_online` with this exact workflow name first."
                )
        else:
            domain = entry.domain if entry else route.domain
            if domain in ("sg", "sg_kb", "sg_carpark", "sg_plot", "sg_company"):
                workflow_hint = (
                    "\nSingapore (Zaha) question: use ONLY sg-old MCP tools. "
                    "Do NOT call run_competency_online, run_workflow_online, or any mof-twa tools. "
                    "Prefer dedicated answer tools when available "
                    "(e.g. get_sg_q15_jurong_answer, count_sg_within_max_gfa)."
                )
            elif domain == "mops":
                workflow_hint = (
                    "\nMOP polyhedra question: use twa-mops and/or chemistry-ontomops tools only. "
                    "Do NOT use mof-twa Metal-Organic Framework corpus tools."
                )
            elif entry and entry.domain == "chemistry":
                workflow_hint = (
                    f"\nChemistry question (catalog id `{entry.id}`): use chemistry MCP atomic tools only. "
                    "Do NOT use mof-twa."
                )

        servers = ", ".join(route.mcp_servers)
        return f"""You are a KGQA assistant for The World Avatar knowledge graphs.

**Loaded MCP servers:** {servers}
**Routing:** {route.reason} (domain: {route.domain})
{workflow_hint}

**User question:**
{question}

**Online → offline policy (mandatory):**
1. Use MCP tools to answer with **online probe limits** (compact TSV responses).
2. For multi-step questions, use `run_workflow_online` or `run_competency_online` when a named workflow applies.
3. When an online run returns `recording_path`, **stop tool use** and summarize the compact online answer.
4. **Do NOT** call `replay_workflow_offline` or `replay_competency_offline` yourself — offline full-scale replay runs outside the LLM.
5. Always include the `recording_path` value from tool output in your final answer when present.

Provide a clear, structured answer citing key numeric results from tool output.
"""
