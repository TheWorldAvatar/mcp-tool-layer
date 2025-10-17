# mcp_run_agent_hint_only_dynamic.py
import os, argparse, asyncio, shutil, json, time, random, tempfile
from typing import List, Dict
from filelock import FileLock
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from src.agents.mops.dynamic_mcp.modules.kg import parse_top_level_entities
from src.agents.mops.dynamic_mcp.modules.extraction import extract_content
# dynamic namespaces, do NOT import specific names
from src.agents.mops.dynamic_mcp.prompts import prompts as prompts_ns
from src.agents.mops.dynamic_mcp.prompts import extraction_scopes as scopes_ns
from src.agents.mops.dynamic_mcp.modules.prompt_utils import (
    collect_scopes, collect_prompts, format_prompt
)

log = get_logger("agent", "MainDynamic")

# -------------------- Global state writer --------------------
GLOBAL_STATE_DIR = "data"
GLOBAL_STATE_JSON = os.path.join(GLOBAL_STATE_DIR, "global_state.json")
GLOBAL_STATE_LOCK = os.path.join(GLOBAL_STATE_DIR, "global_state.lock")

def write_global_state(doi: str, top_level_entity_name: str):
    """Write global state atomically with file lock for MCP server to read."""
    os.makedirs(GLOBAL_STATE_DIR, exist_ok=True)
    lock = FileLock(GLOBAL_STATE_LOCK)
    lock.acquire(timeout=30.0)
    try:
        state = {"doi": doi, "top_level_entity_name": top_level_entity_name}
        fd, tmp = tempfile.mkstemp(dir=GLOBAL_STATE_DIR, suffix=".json.tmp")
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, GLOBAL_STATE_JSON)
        log.info(f"Global state written: doi={doi}, entity={top_level_entity_name}")
    finally:
        lock.release()

# -------------------- FS helpers --------------------

def find_tasks(root="data"):
    return [d for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))
            and not d.startswith('.')
            and d not in ['log', 'ontologies']]

def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def _write_text(path: str, content: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def _discover_tbox(paper_md_path: str) -> str:
    root = os.path.dirname(paper_md_path)
    tbox_path = os.path.join(root, "ontosynthesis.ttl")
    return _read_text(tbox_path) if os.path.exists(tbox_path) else ""

def _agent(tools=None, model="gpt-4o", temp=0.1, top_p=0.1, mcp_set="run_created_mcp.json"):
    return BaseAgent(
        model_name=model,
        model_config=ModelConfig(temperature=temp, top_p=top_p),
        remote_model=True,
        mcp_tools=tools or [],
        mcp_set_name=mcp_set,
    )

def _safe_name(label: str) -> str:
    return (label or "entity").replace(" ", "_").replace("/", "_")

# -------------------- Retry wrapper --------------------
async def _run_agent_with_retry(agent, instruction: str, max_retries: int = 3, recursion_limit: int = 600):
    """
    Run agent with retry mechanism.
    
    Args:
        agent: The agent instance to run
        instruction: The instruction to pass to the agent
        max_retries: Maximum number of retry attempts (default 3 total attempts)
        recursion_limit: Recursion limit for agent execution
        
    Returns:
        tuple: (response, metadata) from successful agent execution
        
    Raises:
        RuntimeError: If all retry attempts fail
    """
    retries = 0
    last_error = None
    
    while retries < max_retries:
        try:
            log.info(f"Agent execution attempt {retries + 1}/{max_retries}")
            response, metadata = await agent.run(instruction, recursion_limit=recursion_limit)
            log.info(f"Agent execution succeeded on attempt {retries + 1}")
            return response, metadata
        except Exception as e:
            last_error = e
            retries += 1
            log.error(f"Agent execution failed on attempt {retries}/{max_retries}: {e}")
            
            if retries < max_retries:
                wait_time = 5 * retries  # Progressive backoff: 5s, 10s, 15s
                log.info(f"Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
            else:
                log.error(f"Agent execution failed after {max_retries} attempts")
    
    raise RuntimeError(f"Agent execution failed after {max_retries} attempts. Last error: {last_error}")

# -------------------- Rate limiter --------------------
class RateLimiter:
    def __init__(self, min_delay=1.0, max_delay=2.0):
        self.min_delay = float(min_delay)
        self.max_delay = float(max_delay)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self):
        async with self._lock:
            now = time.time()
            dt = now - self._last
            need = random.uniform(self.min_delay, self.max_delay)
            if dt < need:
                await asyncio.sleep(need - dt)
            self._last = time.time()

# -------------------- Core runner --------------------
async def run_task(doi: str, test: bool = False):
    doi_dir = os.path.join("data", doi)
    md_path = os.path.join(doi_dir, f"{doi}_stitched.md")
    if not os.path.exists(md_path):
        print(f"Skipping {doi}: _stitched.md file not found")
        return

    paper = _read_text(md_path)
    t_box = _discover_tbox(md_path)

    scopes = collect_scopes(scopes_ns)       # [(iter, scope_text), ...] sorted
    prompt_map = collect_prompts(prompts_ns) # {iter: template, ...}
    if not scopes:
        print("No EXTRACTION_SCOPE_* found. Nothing to run.")
        return

    hints_dir = os.path.join(doi_dir, "mcp_run")
    os.makedirs(hints_dir, exist_ok=True)

    intermediate_ttl_dir = os.path.join(doi_dir, "intermediate_ttl_files")
    os.makedirs(intermediate_ttl_dir, exist_ok=True)

    # ----- Iteration 1 -----
    iter1_scope = next(((i, s) for (i, s) in scopes if i == 1), None)
    if iter1_scope:
        i1, scope_text_1 = iter1_scope
        response_file_1 = os.path.join(hints_dir, "iter1_response.md")

        if os.path.exists(response_file_1):
            print(f"⏭️  Skip entire iteration {i1}: {response_file_1} exists")
        else:
            hint_file_1 = os.path.join(hints_dir, f"iter{i1}_hints.txt")
            if os.path.exists(hint_file_1):
                print(f"⏭️  Skip extraction iter {i1}: {hint_file_1} exists")
                hints1 = _read_text(hint_file_1)
            else:
                print(f"🔍 Extracting hints for iteration {i1} (global)...")
                hints1 = await extract_content(
                    paper_content=paper,
                    goal=scope_text_1,
                    t_box=t_box,
                    entity_label=None,
                    entity_uri=None,
                )
                _write_text(hint_file_1, hints1)
                print(f"✅ Saved hints to {hint_file_1}")

            if i1 in prompt_map:
                tmpl1 = prompt_map[i1]
                instr1 = format_prompt(
                    tmpl1,
                    doi=doi,
                    iteration=i1,
                    paper_content=_read_text(hint_file_1),
                )
                _write_text(os.path.join(hints_dir, "iter1_instruction.md"),
                            f"# Iteration {i1} — Instruction\n\n{instr1}")

                iteration_1_ttl = os.path.join(doi_dir, "iteration_1.ttl")
                if os.path.exists(iteration_1_ttl):
                    print(f"⏭️  Skip iter {i1} execution: {iteration_1_ttl} exists")
                else:
                    print("🚀 Running iteration 1 agent...")
                    # Write global state for iteration 1 (top-level entities)
                    write_global_state(doi, "top")
                    agent1 = _agent(tools=["llm_created_mcp"])
                    try:
                        resp1, _ = await _run_agent_with_retry(agent1, instr1, max_retries=3, recursion_limit=600)
                        _write_text(response_file_1,
                                    f"# Iteration {i1}\n\n## Instruction\n\n{instr1}\n\n## Response\n\n{resp1}")
                        out_local = os.path.join(doi_dir, "output.ttl")
                        if os.path.exists(out_local):
                            shutil.copy2(out_local, iteration_1_ttl)
                    except RuntimeError as e:
                        log.error(f"Failed to complete iteration 1 after retries: {e}")
                        print(f"❌ Failed to complete iteration 1 after retries: {e}")
            else:
                print("⚠️  No prompt for iteration 1; continuing.")
    else:
        print("⚠️  No iteration 1 scope found; continuing without top entities.")

    # Parse top-level entities from output_top.ttl (iteration 1 output)
    if test:
        top_entities = parse_top_level_entities(doi, output_file="output_top.ttl")[:1]  # first entity only
    else:
        top_entities = parse_top_level_entities(doi, output_file="output_top.ttl") or []

    _write_text(os.path.join(hints_dir, "iter1_top_entities.json"), json.dumps(top_entities, indent=2))

    # Iterations >= 2 for test mode: single entity through all iterations
    if test and top_entities:
        async def _test_mode_single_entity():
            e = top_entities[0]  # only the first entity
            label = e.get("label", "")
            uri = e.get("uri", "")
            safe = _safe_name(label)

            # Run through all iterations >= 2 for the first entity
            for iter_no, scope_text in scopes:
                if iter_no == 1:  # Skip iteration 1 (already handled above)
                    continue
                    
                print(f"🔄 Test mode: Processing iteration {iter_no} for entity '{label}'...")

                # Check if hints already exist
                hint_file = os.path.join(hints_dir, f"iter{iter_no}_hints_{safe}.txt")
                if os.path.exists(hint_file):
                    print(f"⏭️  Skip test extraction for '{label}' iter {iter_no}: {hint_file} exists")
                    hints = _read_text(hint_file)
                else:
                    print(f"🔍 Test mode: Extracting for entity '{label}' iter {iter_no}...")
                    hints = await extract_content(
                        paper_content=paper,
                        goal=scope_text,
                        t_box=t_box,
                        entity_label=label,
                        entity_uri=uri,
                    )
                    print(f"✅ Test mode: Completed extraction for '{label}' iter {iter_no}.")
                    
                    # Save hints
                    _write_text(hint_file, hints)
                    print(f"✅ Test mode: Saved hints to {hint_file}")
                
                # Run the actual agent execution for test mode
                if iter_no in prompt_map:
                    tmpl = prompt_map[iter_no]
                    response_file = os.path.join(hints_dir, f"iter{iter_no}_{safe}.md")
                    
                    if os.path.exists(response_file):
                        print(f"⏭️  Skip test execution for '{label}' iter {iter_no}: response exists")
                    else:
                        print(f"🚀 Test mode: Running iteration {iter_no} for entity: {label}")
                        instr = format_prompt(
                            tmpl,
                            doi=doi,
                            iteration=iter_no,
                            entity_label=label,
                            entity_uri=uri,
                            paper_content=hints,
                        )
                        
                        _write_text(os.path.join(hints_dir, f"iter{iter_no}_{safe}_instruction.md"),
                                    f"# Iteration {iter_no} — {label} — Instruction\n\n{instr}")
                        
                        # Write global state for this entity
                        write_global_state(doi, safe)
                        agent = _agent(tools=["llm_created_mcp"])
                        try:
                            resp, _ = await _run_agent_with_retry(agent, instr, max_retries=3, recursion_limit=600)
                            _write_text(response_file,
                                        f"# Iteration {iter_no} — {label}\n\n## Instruction\n\n{instr}\n\n## Response\n\n{resp}")
                            
                            # Copy output.ttl if it exists
                            out_local = os.path.join(doi_dir, "output.ttl")
                            if os.path.exists(out_local):
                                intermediate_ttl = os.path.join(intermediate_ttl_dir, f"iteration_{iter_no}_{safe}.ttl")
                                shutil.copy2(out_local, intermediate_ttl)
                                print(f"✅ Test mode: Saved intermediate TTL to {intermediate_ttl}")
                        except RuntimeError as e:
                            log.error(f"Test mode: Failed to complete iter {iter_no} for '{label}' after retries: {e}")
                            print(f"❌ Test mode: Failed to complete iter {iter_no} for '{label}' after retries: {e}")
                else:
                    print(f"⚠️  No prompt for iteration {iter_no}; test mode completed extraction only.")

        await _test_mode_single_entity()
        return

    # Iterations >= 2: parallel per-entity extraction with batch size 8 and 1–2s spacing
    async def _extract_entity(iter_no: int, scope_text: str, e: Dict[str, str],
                              semaphore: asyncio.Semaphore, rl: RateLimiter):
        label = e.get("label", "")
        uri = e.get("uri", "")
        safe = _safe_name(label)
        response_file = os.path.join(hints_dir, f"iter{iter_no}_{safe}.md")
        if os.path.exists(response_file):
            print(f"⏭️  Skip entity '{label}' for iter {iter_no}: response exists")
            return

        hint_file = os.path.join(hints_dir, f"iter{iter_no}_hints_{safe}.txt")
        if os.path.exists(hint_file):
            print(f"⏭️  Skip extraction iter {iter_no} for '{label}': {hint_file} exists")
            return

        async with semaphore:
            await rl.wait()  # 1–2s spacing across tasks
            print(f"🔍 Extracting (iter {iter_no}) for entity '{label}'...")
            hints = await extract_content(
                paper_content=paper,
                goal=scope_text,
                t_box=t_box,
                entity_label=label,
                entity_uri=uri,
            )
            _write_text(hint_file, hints)
            print(f"✅ Saved {hint_file}")

    for iter_no, scope_text in scopes:
        if iter_no == 1 or (test and iter_no != 2):
            continue
        if not top_entities:
            print(f"⚠️  No top entities for iter {iter_no}.")
            continue

        # build job list honoring task-wise skip before scheduling
        jobs: List[Dict[str, str]] = []
        for e in top_entities:
            label = e.get("label", "")
            safe = _safe_name(label)
            response_file = os.path.join(hints_dir, f"iter{iter_no}_{safe}.md")
            hint_file = os.path.join(hints_dir, f"iter{iter_no}_hints_{safe}.txt")
            if os.path.exists(response_file):
                print(f"⏭️  Skip entity '{label}' for iter {iter_no}: response exists")
                continue
            if os.path.exists(hint_file):
                print(f"⏭️  Skip extraction iter {iter_no} for '{label}': {hint_file} exists")
                continue
            jobs.append(e)

        if not jobs:
            continue

        print(f"🚦 Parallel extraction iter {iter_no}: {len(jobs)} entity jobs")
        sem = asyncio.Semaphore(8)          # batch size/concurrency cap
        rate = RateLimiter(1.0, 2.0)        # 1–2s between starts
        tasks = [asyncio.create_task(_extract_entity(iter_no, scope_text, e, sem, rate)) 
                 for e in jobs]
        await asyncio.gather(*tasks)

    if test:
        return

    # Iterations >= 2: per-entity execution with SINGLE hint
    for iter_no, _ in scopes:
        if iter_no == 1:
            continue
        if iter_no not in prompt_map:
            continue

        tmpl = prompt_map[iter_no]
        if not top_entities:
            print(f"⚠️  No entities to run for iter {iter_no}.")
            continue

        for e in top_entities:
            label = e.get("label", "")
            uri = e.get("uri", "")
            safe = _safe_name(label)

            response_file = os.path.join(hints_dir, f"iter{iter_no}_{safe}.md")
            if os.path.exists(response_file):
                print(f"⏭️  Skip iter {iter_no} for '{label}': response exists")
                continue

            hint_file = os.path.join(hints_dir, f"iter{iter_no}_hints_{safe}.txt")
            if not os.path.exists(hint_file):
                print(f"⚠️  Missing hints for iter {iter_no}, entity '{label}'. Skipping.")
                continue

            single_hint = _read_text(hint_file)
            instr = format_prompt(
                tmpl,
                doi=doi,
                iteration=iter_no,
                entity_label=label,
                entity_uri=uri,
                paper_content=single_hint,
            )

            _write_text(os.path.join(hints_dir, f"iter{iter_no}_{safe}_instruction.md"),
                        f"# Iteration {iter_no} — {label} — Instruction\n\n{instr}")

            print(f"🚀 Running iteration {iter_no} for entity: {label}")
            # Write global state for this entity
            write_global_state(doi, safe)
            agent = _agent(tools=["llm_created_mcp"])
            try:
                resp, _ = await _run_agent_with_retry(agent, instr, max_retries=3, recursion_limit=600)
                _write_text(response_file,
                            f"# Iteration {iter_no} — {label}\n\n## Instruction\n\n{instr}\n\n## Response\n\n{resp}")

                out_local = os.path.join(doi_dir, "output.ttl")
                if os.path.exists(out_local):
                    intermediate_ttl = os.path.join(intermediate_ttl_dir, f"iteration_{iter_no}_{safe}.ttl")
                    shutil.copy2(out_local, intermediate_ttl)
                    print(f"✅ Saved intermediate TTL to {intermediate_ttl}")
            except RuntimeError as e:
                log.error(f"Failed to complete iter {iter_no} for entity '{label}' after retries: {e}")
                print(f"❌ Failed to complete iter {iter_no} for entity '{label}' after retries: {e}")

# -------------------- CLI --------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument('--single', type=str, help='DOI folder to run once')
    ap.add_argument('--test', action='store_true', help='Dry run: prepare hints and instructions only')
    args = ap.parse_args()

    if args.single:
        asyncio.run(run_task(args.single, test=args.test))
    else:
        tasks = find_tasks()
        print(f"Running in {'TEST' if args.test else 'normal'} mode with {len(tasks)} DOI folders")
        for doi in tasks:
            md = os.path.join("data", doi, f"{doi}_stitched.md")
            if os.path.exists(md):
                print(f"Processing: {doi}")
                asyncio.run(run_task(doi, test=args.test))
            else:
                print(f"Skipping {doi}: _stitched.md file not found")
