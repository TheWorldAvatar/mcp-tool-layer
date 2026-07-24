"""KGQA orchestrator: route → online ReAct → offline replay."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from mini_marie.kgqa.agent import KgqaAgent
from mini_marie.kgqa.mcp_router import route_question
from mini_marie.kgqa.offline_runner import replay_offline
from mini_marie.kgqa.recording_utils import extract_recording_info


def _route_dict(route) -> Dict[str, Any]:
    if isinstance(route, dict):
        return route
    return {
        "mcp_servers": route.mcp_servers,
        "domain": route.domain,
        "reason": route.reason,
        "catalog_entry_id": route.catalog_entry.id if route.catalog_entry else None,
    }


def _build_result(
    *,
    question: str,
    route,
    online_answer: str,
    metadata: Dict[str, Any],
    recording_path: Optional[str],
    workflow_id: Optional[str],
    offline_result: Optional[Dict[str, Any]],
    timing: Dict[str, Any],
) -> Dict[str, Any]:
    total = timing.get("total_ms")
    if total is None:
        total = (timing.get("route_ms") or 0) + (timing.get("online_ms") or 0) + (timing.get("offline_ms") or 0)
    return {
        "question": question,
        "online_answer": online_answer,
        "recording_path": recording_path,
        "workflow_id": workflow_id,
        "route": _route_dict(route),
        "metadata": metadata,
        "offline": offline_result,
        "offline_recording_path": (offline_result or {}).get("offline_path"),
        "timing": timing,
        "elapsed_ms": total,
    }


async def run_online_phase_async(
    question: str,
    *,
    model_name: str = "gpt-4o-mini",
    remote_model: bool = True,
    recursion_limit: int = 200,
) -> Dict[str, Any]:
    """Online ReAct only — returns partial result for GUI phase 1."""
    t0 = time.perf_counter()
    route = route_question(question)
    route_ms = round((time.perf_counter() - t0) * 1000)

    t1 = time.perf_counter()
    agent = KgqaAgent(model_name=model_name, remote_model=remote_model)
    online_answer, metadata = await agent.ask(
        question,
        route=route,
        recursion_limit=recursion_limit,
    )
    online_ms = round((time.perf_counter() - t1) * 1000)

    rec_info = extract_recording_info(online_answer=online_answer, metadata=metadata)
    recording_path = rec_info.get("recording_path")
    workflow_id = rec_info.get("workflow_id") or metadata.get("workflow_id")

    return _build_result(
        question=question,
        route=route,
        online_answer=online_answer,
        metadata=metadata,
        recording_path=recording_path,
        workflow_id=workflow_id,
        offline_result=None,
        timing={
            "route_ms": route_ms,
            "online_ms": online_ms,
            "offline_ms": 0,
            "total_ms": route_ms + online_ms,
        },
    )


def run_offline_phase(
    partial: Dict[str, Any],
    *,
    offline_cap: int = 500_000,
    skip_offline_if_no_cache: bool = True,
) -> Dict[str, Any]:
    """Offline replay from online partial result — returns completed result."""
    recording_path = partial.get("recording_path")
    workflow_id = partial.get("workflow_id")
    offline_result: Optional[Dict[str, Any]] = None
    offline_ms = 0

    if recording_path:
        t0 = time.perf_counter()
        offline_result = replay_offline(
            recording_path,
            workflow_id=workflow_id,
            offline_cap=offline_cap,
        )
        offline_ms = round((time.perf_counter() - t0) * 1000)
        if (
            skip_offline_if_no_cache
            and offline_result.get("status") == "error"
            and offline_result.get("error")
        ):
            err = str(offline_result.get("error", "")).lower()
            if "cache" in err or "not found" in err:
                offline_result["skipped"] = True

    timing = dict(partial.get("timing") or {})
    timing["offline_ms"] = offline_ms
    timing["total_ms"] = (timing.get("route_ms") or 0) + (timing.get("online_ms") or 0) + offline_ms

    return _build_result(
        question=partial["question"],
        route=partial["route"],
        online_answer=partial["online_answer"],
        metadata=partial["metadata"],
        recording_path=recording_path,
        workflow_id=workflow_id,
        offline_result=offline_result,
        timing=timing,
    )


def run_online_phase(
    question: str,
    *,
    model_name: str = "gpt-4o-mini",
    remote_model: bool = True,
    recursion_limit: int = 200,
) -> Dict[str, Any]:
    return asyncio.run(
        run_online_phase_async(
            question,
            model_name=model_name,
            remote_model=remote_model,
            recursion_limit=recursion_limit,
        )
    )


async def run_kgqa_async(
    question: str,
    *,
    model_name: str = "gpt-4o-mini",
    remote_model: bool = True,
    recursion_limit: int = 200,
    auto_offline: bool = True,
    skip_offline_if_no_cache: bool = True,
    offline_cap: int = 500_000,
) -> Dict[str, Any]:
    started = time.perf_counter()
    t_route = time.perf_counter()
    route = route_question(question)
    route_ms = round((time.perf_counter() - t_route) * 1000)

    agent = KgqaAgent(model_name=model_name, remote_model=remote_model)
    t_online = time.perf_counter()
    online_answer, metadata = await agent.ask(
        question,
        route=route,
        recursion_limit=recursion_limit,
    )
    online_ms = round((time.perf_counter() - t_online) * 1000)

    rec_info = extract_recording_info(online_answer=online_answer, metadata=metadata)
    recording_path = rec_info.get("recording_path")
    workflow_id = rec_info.get("workflow_id") or metadata.get("workflow_id")

    offline_result: Optional[Dict[str, Any]] = None
    offline_ms = 0
    if auto_offline and recording_path:
        t_off = time.perf_counter()
        offline_result = replay_offline(
            recording_path,
            workflow_id=workflow_id,
            offline_cap=offline_cap,
        )
        offline_ms = round((time.perf_counter() - t_off) * 1000)
        if (
            skip_offline_if_no_cache
            and offline_result.get("status") == "error"
            and offline_result.get("error")
        ):
            err = str(offline_result.get("error", "")).lower()
            if "cache" in err or "not found" in err:
                offline_result["skipped"] = True

    total_ms = round((time.perf_counter() - started) * 1000)
    return _build_result(
        question=question,
        route=route,
        online_answer=online_answer,
        metadata=metadata,
        recording_path=recording_path,
        workflow_id=workflow_id,
        offline_result=offline_result,
        timing={
            "route_ms": route_ms,
            "online_ms": online_ms,
            "offline_ms": offline_ms,
            "total_ms": total_ms,
        },
    )


def run_kgqa(
    question: str,
    *,
    model_name: str = "gpt-4o-mini",
    remote_model: bool = True,
    recursion_limit: int = 200,
    auto_offline: bool = True,
    skip_offline_if_no_cache: bool = True,
    offline_cap: int = 500_000,
) -> Dict[str, Any]:
    """Synchronous entry point for Streamlit and CLI."""
    return asyncio.run(
        run_kgqa_async(
            question,
            model_name=model_name,
            remote_model=remote_model,
            recursion_limit=recursion_limit,
            auto_offline=auto_offline,
            skip_offline_if_no_cache=skip_offline_if_no_cache,
            offline_cap=offline_cap,
        )
    )
