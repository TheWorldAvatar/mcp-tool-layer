"""One-shot full-cycle KGQA demo with per-phase timing (online ReAct + offline replay)."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from mini_marie.cache_paths import repo_root as REPO_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mini_marie.kgqa.agent import KgqaAgent
from mini_marie.kgqa.mcp_router import route_question
from mini_marie.kgqa.offline_runner import replay_offline
from mini_marie.kgqa.recording_utils import extract_recording_info

DEFAULT_QUESTION = "Average and variance PLD of UiO-66"


async def run_full_cycle(question: str = DEFAULT_QUESTION) -> None:
    t0 = time.perf_counter()

    route = route_question(question)
    t_route_ms = round((time.perf_counter() - t0) * 1000)

    t1 = time.perf_counter()
    agent = KgqaAgent(model_name="gpt-4o-mini")
    online_answer, metadata = await agent.ask(question, route=route, recursion_limit=80)
    t_online_ms = round((time.perf_counter() - t1) * 1000)

    rec_info = extract_recording_info(online_answer=online_answer, metadata=metadata)
    recording_path = rec_info.get("recording_path")
    workflow_id = rec_info.get("workflow_id") or metadata.get("workflow_id")

    t2 = time.perf_counter()
    offline = replay_offline(recording_path, workflow_id=workflow_id) if recording_path else None
    t_offline_ms = round((time.perf_counter() - t2) * 1000) if recording_path else 0

    t_total_ms = round((time.perf_counter() - t0) * 1000)
    usage = metadata.get("aggregated_usage") or {}
    tools = metadata.get("tool_activity", {}).get("executed_tool_name_set") or []

    print("=" * 72)
    print("KGQA FULL CYCLE RUN")
    print("=" * 72)
    print(f"Question: {question}\n")

    print("--- TIMING ---")
    print(f"  Route:          {t_route_ms:>7} ms")
    print(f"  Online (ReAct): {t_online_ms:>7} ms")
    print(f"  Offline replay: {t_offline_ms:>7} ms")
    print(f"  Total:          {t_total_ms:>7} ms\n")

    print("--- ROUTE ---")
    print(f"  MCP servers: {route.mcp_servers}")
    print(f"  Domain: {route.domain} | Reason: {route.reason}")
    if route.catalog_entry:
        print(f"  Catalog entry: {route.catalog_entry.id} (workflow: {route.catalog_entry.workflow_id})")
    print(f"  Tools called: {tools}")
    print(
        f"  LLM: {usage.get('total_tokens')} tokens, "
        f"{usage.get('calls')} calls, ${usage.get('total_cost_usd', 0):.4f}\n"
    )

    print("--- ONLINE PHASE (LLM + MCP probe) ---")
    print(f"  Recording file:\n  {recording_path}\n")
    print(online_answer)
    print()

    print("--- OFFLINE PHASE (no LLM, full cache replay) ---")
    if not offline:
        print("  Skipped — no recording_path from online phase.")
    else:
        print(f"  Status:     {offline.get('status')}")
        print(f"  Offline file:\n  {offline.get('offline_path')}")
        print(f"  Row count:  {offline.get('row_count')}")
        print(f"  Answer:     {offline.get('answer')}")

        op = offline.get("offline_path")
        if op and Path(op).exists():
            payload = json.loads(Path(op).read_text(encoding="utf-8"))
            digest = payload.get("answer_digest") or {}
            if digest.get("authoritative"):
                print(f"  Authoritative metrics: {json.dumps(digest['authoritative'], indent=2)}")
            trace = payload.get("call_trace") or []
            if trace and trace[0].get("rows"):
                print(f"  Full rows (step 1, from file):\n{json.dumps(trace[0]['rows'], indent=2)}")

    print("=" * 72)


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    asyncio.run(run_full_cycle(q))
