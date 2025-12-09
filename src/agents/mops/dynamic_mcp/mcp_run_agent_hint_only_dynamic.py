# mcp_run_agent_hint_only_dynamic.py
import os, argparse, asyncio, shutil, json, time, random, tempfile
from typing import List, Dict
from filelock import FileLock
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.LLMCreator import LLMCreator
from src.utils.global_logger import get_logger
from src.agents.mops.dynamic_mcp.modules.kg import parse_top_level_entities
from src.agents.mops.dynamic_mcp.modules.extraction import extract_content
from src.utils.extraction_models import get_extraction_model
# dynamic namespaces, do NOT import specific names
from src.agents.mops.dynamic_mcp.prompts import prompts as prompts_ns
from src.agents.mops.dynamic_mcp.prompts import extraction_scopes as scopes_ns
from src.agents.mops.dynamic_mcp.modules.prompt_utils import (
    collect_scopes, collect_prompts, format_prompt
)
# Import pre-extraction logic from llm_based.py
from tests.step_extraction.llm_based import build_text_extraction_prompt

log = get_logger("agent", "MainDynamic")

# -------------------- Global state writer --------------------
GLOBAL_STATE_DIR = "data"
GLOBAL_STATE_JSON = os.path.join(GLOBAL_STATE_DIR, "global_state.json")
GLOBAL_STATE_LOCK = os.path.join(GLOBAL_STATE_DIR, "global_state.lock")

def write_global_state(doi: str, top_level_entity_name: str, top_level_entity_iri: str | None = None):
    """Write global state atomically with file lock for MCP server to read."""
    os.makedirs(GLOBAL_STATE_DIR, exist_ok=True)
    lock = FileLock(GLOBAL_STATE_LOCK)
    lock.acquire(timeout=30.0)
    try:
        state = {"doi": doi, "top_level_entity_name": top_level_entity_name}
        if top_level_entity_iri:
            state["top_level_entity_iri"] = top_level_entity_iri
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
    # Allow override via env var to keep iter-specific model choices controllable from the driver
    model_override = os.environ.get("MOPS_EXTRACTION_MODEL") or model
    return BaseAgent(
        model_name=model_override,
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
                    model_name=get_extraction_model("iter1_hints"),
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
                        top_local = os.path.join(doi_dir, "output_top.ttl")
                        if os.path.exists(out_local):
                            shutil.copy2(out_local, iteration_1_ttl)
                            print(f"✅ Saved iteration_1.ttl from output.ttl")
                        elif os.path.exists(top_local):
                            shutil.copy2(top_local, iteration_1_ttl)
                            print(f"✅ Saved iteration_1.ttl from output_top.ttl")
                        else:
                            print("⚠️ No TTL produced for iteration 1 (neither output.ttl nor output_top.ttl found)")
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
                        model_name=get_extraction_model(f"iter{iter_no}_hints"),
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
                        instr += ("\n\n"
                                  "Before exporting the final TTL/memory, call the tool `check_orphan_entities` to detect any orphan entities. "
                                  "If any are found, attempt to connect them appropriately (e.g., attach to synthesis, steps, IO, or parameters). "
                                  "If you cannot connect some, list their details in your response and proceed with export.")
                        
                        _write_text(os.path.join(hints_dir, f"iter{iter_no}_{safe}_instruction.md"),
                                    f"# Iteration {iter_no} — {label} — Instruction\n\n{instr}")
                        
                        # Write global state for this entity (include entity IRI)
                        write_global_state(doi, safe, uri)
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

            # Pre-extraction for iter3: extract raw relevant text spans for this entity
            source_text = paper
            if iter_no == 3:
                llm_out_dir = os.path.join(doi_dir, "llm_based_results")
                os.makedirs(llm_out_dir, exist_ok=True)
                entity_text_path = os.path.join(llm_out_dir, f"entity_text_{safe}.txt")

                pre_text = ""
                if os.path.exists(entity_text_path):
                    try:
                        with open(entity_text_path, "r", encoding="utf-8") as f:
                            pre_text = f.read().strip()
                    except Exception:
                        pre_text = ""
                
                # If we have pre-text (cached), still save the prompt for comparison
                if pre_text:
                    try:
                        pre_prompt = build_text_extraction_prompt(label, paper)
                        
                        # Get actual model name from config
                        model_name = get_extraction_model("iter3_pre_extraction")
                        
                        # Add model info at the end
                        pre_prompt_with_meta = f"""{pre_prompt}

---
EFFECTIVE MODEL CONFIGURATION (mcp_run - cached):
Model: {model_name}
Temperature: 0
Top_p: 1.0
Remote: True
Config source: configs/extraction_models.json -> iter3_pre_extraction
"""
                        
                        prompt_comparison_dir = os.path.join(doi_dir, "prompt_comparison", "mcp_run")
                        os.makedirs(prompt_comparison_dir, exist_ok=True)
                        prompt_comparison_path = os.path.join(prompt_comparison_dir, f"pre_extraction_prompt_{safe}.txt")
                        with open(prompt_comparison_path, "w", encoding="utf-8") as f:
                            f.write(pre_prompt_with_meta)
                    except Exception:
                        pass

                if not pre_text:
                    # Use the exact same pre-extraction logic as llm_based.py
                    try:
                        print(f"🔍 Running pre-extraction for entity '{label}' using llm_based.py logic...")
                        
                        # Build prompt using llm_based.py function (handles transformation detection internally)
                        pre_prompt = build_text_extraction_prompt(label, paper)
                        
                        # Get actual model name from config
                        model_name = get_extraction_model("iter3_pre_extraction")
                        print(f"📝 Using model from config: {model_name}")
                        
                        # Add model info at the end
                        pre_prompt_with_meta = f"""{pre_prompt}

---
EFFECTIVE MODEL CONFIGURATION (mcp_run):
Model: {model_name}
Temperature: 0
Top_p: 1.0
Remote: True
Config source: configs/extraction_models.json -> iter3_pre_extraction
"""
                        
                        # Save prompt for comparison
                        try:
                            prompt_comparison_dir = os.path.join(doi_dir, "prompt_comparison", "mcp_run")
                            os.makedirs(prompt_comparison_dir, exist_ok=True)
                            prompt_comparison_path = os.path.join(prompt_comparison_dir, f"pre_extraction_prompt_{safe}.txt")
                            with open(prompt_comparison_path, "w", encoding="utf-8") as f:
                                f.write(pre_prompt_with_meta)
                        except Exception:
                            pass
                        
                        # Create LLM instance with same config as llm_based.py
                        llm_pre = LLMCreator(
                            model=model_name,
                            model_config=ModelConfig(temperature=0, top_p=1.0),
                            remote_model=True,
                        ).setup_llm()
                        
                        # Extract with retries (matching llm_based.py)
                        for attempt in range(2):
                            try:
                                resp = llm_pre.invoke(pre_prompt)
                                pre_text = str(getattr(resp, "content", resp) or "").strip()
                                if pre_text:
                                    break
                            except Exception as invoke_err:
                                if attempt == 1:
                                    print(f"⚠️  Pre-extraction failed after retries: {invoke_err}")
                                    pre_text = ""
                        
                        if pre_text:
                            try:
                                with open(entity_text_path, "w", encoding="utf-8") as f:
                                    f.write(pre_text)
                                print(f"✅ Saved pre-extraction text to {entity_text_path}")
                            except Exception as write_err:
                                print(f"⚠️  Failed to write pre-extraction text: {write_err}")
                        else:
                            print(f"⚠️  Pre-extraction returned empty text for '{label}'")
                    except Exception as e:
                        print(f"⚠️  Pre-extraction setup failed for '{label}': {e}")
                        pre_text = ""

                if pre_text:
                    source_text = pre_text
                    print(f"✅ Using pre-extracted text for '{label}' ({len(pre_text)} chars)")
                else:
                    print(f"⚠️  Using full paper for '{label}' (pre-extraction unavailable)")

            # Prepare prompt comparison path for iter3
            save_prompt_path = None
            if iter_no == 3:
                prompt_comparison_dir = os.path.join(doi_dir, "prompt_comparison", "mcp_run")
                os.makedirs(prompt_comparison_dir, exist_ok=True)
                save_prompt_path = os.path.join(prompt_comparison_dir, f"iter3_prompt_{safe}.txt")
                
                # Get actual model name from config
                iter3_model = get_extraction_model("iter3_hints")
                print(f"📝 Using iter3 model from config: {iter3_model}")
                
                # Format iter3 prompt to match llm_based.py exactly
                # Format: [PROMPT]\n\nentity_label: <label>\ncontext:\n<<<\n[text]\n>>>\nOnly output...
                formatted_goal = f"""{scope_text}

entity_label: {label}
context:
<<<
{source_text}
>>>
Only output the JSON object as specified. The MCP tool is a placeholder and does nothing; do not rely on tools.

---
EFFECTIVE MODEL CONFIGURATION (mcp_run):
Model: {iter3_model}
Temperature: 0
Top_p: 1
Remote: True
Config source: configs/extraction_models.json -> iter3_hints
"""
                
                hints = await extract_content(
                    paper_content="",  # Empty because we included it in formatted_goal
                    goal=formatted_goal,
                    t_box="",
                    entity_label=label,
                    entity_uri=uri,
                    model_name=iter3_model,
                    use_raw_prompt=True,
                    save_prompt_path=save_prompt_path,
                )
            else:
                hints = await extract_content(
                    paper_content=source_text,
                    goal=scope_text,
                    t_box=t_box,
                    entity_label=label,
                    entity_uri=uri,
                    model_name=get_extraction_model(f"iter{iter_no}_hints"),
                    use_raw_prompt=False,
                    save_prompt_path=save_prompt_path,
                )
            _write_text(hint_file, hints)
            print(f"✅ Saved {hint_file}")
            if iter_no == 3:
                # Also persist a copy under step_type_only_extraction
                extra_dir = os.path.join(doi_dir, "step_type_only_extraction")
                os.makedirs(extra_dir, exist_ok=True)
                extra_file = os.path.join(extra_dir, f"iter3_0_hints_{safe}.txt")
                _write_text(extra_file, hints)
                print(f"✅ Saved step-type-only copy to {extra_file}")

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
    
    # After iter3 completes, run iter3_1 to add detailed step information
    # iter3_1 uses iter3 results + pre-extraction text to enrich the step types with full details
    scope_3_1 = getattr(scopes_ns, "EXTRACTION_SCOPE_3_1", "")
    if scope_3_1 and any(iter_no == 3 for iter_no, _ in scopes):
        print("🔄 Running iter3_1 (detailed step enrichment)...")
        
        # Backup original iter3_hints files before enrichment
        iter3_results_dir = os.path.join(hints_dir, "iter3_results")
        os.makedirs(iter3_results_dir, exist_ok=True)
        print(f"📦 Backing up original iter3_hints files to {iter3_results_dir}")
        for e in top_entities:
            label = e.get("label", "")
            safe = _safe_name(label)
            iter3_hint_file = os.path.join(hints_dir, f"iter3_hints_{safe}.txt")
            iter3_1_done_marker = os.path.join(hints_dir, f"iter3_1_done_{safe}.marker")
            # Only backup if iter3 hints exist and iter3_1 not yet done
            if os.path.exists(iter3_hint_file) and not os.path.exists(iter3_1_done_marker):
                backup_file = os.path.join(iter3_results_dir, f"iter3_hints_{safe}.txt")
                if not os.path.exists(backup_file):  # Don't overwrite existing backups
                    shutil.copy2(iter3_hint_file, backup_file)
                    print(f"  ✓ Backed up iter3_hints_{safe}.txt")
        
        async def _enrich_iter3_entity(e: Dict[str, str], semaphore: asyncio.Semaphore, rl: RateLimiter):
            label = e.get("label", "")
            uri = e.get("uri", "")
            safe = _safe_name(label)
            
            iter3_hint_file = os.path.join(hints_dir, f"iter3_hints_{safe}.txt")
            iter3_1_done_marker = os.path.join(hints_dir, f"iter3_1_done_{safe}.marker")
            
            # Skip if iter3_1 already completed or iter3 hints don't exist
            if os.path.exists(iter3_1_done_marker):
                print(f"⏭️  Skip iter3_1 for '{label}': already enriched")
                return
            if not os.path.exists(iter3_hint_file):
                print(f"⚠️  Skip iter3_1 for '{label}': iter3 hints not found")
                return
            
            async with semaphore:
                await rl.wait()
                print(f"🔍 Running iter3_1 enrichment for entity '{label}'...")
                
                # Read iter3 step types
                iter3_steps = _read_text(iter3_hint_file)

                # Get pre-extraction text (required)
                llm_out_dir = os.path.join(doi_dir, "llm_based_results")
                entity_text_path = os.path.join(llm_out_dir, f"entity_text_{safe}.txt")
                pre_text = ""
                if os.path.exists(entity_text_path):
                    try:
                        pre_text = _read_text(entity_text_path)
                    except Exception:
                        pre_text = ""
                if not pre_text:
                    print(f"⚠️  Skip iter3_1 for '{label}': pre-extraction text not found")
                    return
                source_text = pre_text
                
                # Build enrichment prompt including iter3 step types as guidance
                enrichment_prompt = f"{scope_3_1}\n\nEntity: {label}\n\nIter3 Step Types (for guidance):\n{iter3_steps}\n\nText:\n{source_text}"
                
                # Extract detailed steps
                try:
                    detailed_hints = await extract_content(
                        paper_content=source_text,
                        goal=enrichment_prompt,
                        t_box="",
                        entity_label=None,
                        entity_uri=None,
                        previous_extraction=iter3_steps,
                        model_name=get_extraction_model("iter3_1_enrichment"),
                    )

                    # Overwrite iter3 hints with iter3_1 detailed results
                    _write_text(iter3_hint_file, detailed_hints)
                    # Create done marker
                    _write_text(iter3_1_done_marker, "done")
                    print(f"✅ Enriched iter3 hints for '{label}' with iter3_1 details")
                except Exception as e:
                    print(f"❌ Failed iter3_1 enrichment for '{label}': {e}")
        
        # Build job list for iter3_1
        iter3_1_jobs: List[Dict[str, str]] = []
        for e in top_entities:
            label = e.get("label", "")
            safe = _safe_name(label)
            iter3_1_done_marker = os.path.join(hints_dir, f"iter3_1_done_{safe}.marker")
            iter3_hint_file = os.path.join(hints_dir, f"iter3_hints_{safe}.txt")
            
            if os.path.exists(iter3_1_done_marker):
                print(f"⏭️  Skip iter3_1 for '{label}': already enriched")
                continue
            if not os.path.exists(iter3_hint_file):
                print(f"⚠️  Skip iter3_1 for '{label}': iter3 hints not found")
                continue
            iter3_1_jobs.append(e)
        
        if iter3_1_jobs:
            print(f"🚦 Running iter3_1 enrichment for {len(iter3_1_jobs)} entities")
            sem_3_1 = asyncio.Semaphore(8)
            rate_3_1 = RateLimiter(1.0, 2.0)
            tasks_3_1 = [asyncio.create_task(_enrich_iter3_entity(e, sem_3_1, rate_3_1)) 
                         for e in iter3_1_jobs]
            await asyncio.gather(*tasks_3_1)
            print("✅ iter3_1 enrichment completed")
        else:
            print("⏭️  No entities require iter3_1 enrichment")
    
    # After iter3_1 completes, run iter3_2 to add vessel types and equipment details
    # iter3_2 uses iter3_1 enriched results + pre-extraction text to add vessel type information
    scope_3_2 = getattr(scopes_ns, "EXTRACTION_SCOPE_3_2", "")
    if scope_3_2 and any(iter_no == 3 for iter_no, _ in scopes):
        print("🔄 Running iter3_2 (vessel type & equipment enrichment)...")
        
        async def _enrich_iter3_2_entity(e: Dict[str, str], semaphore: asyncio.Semaphore, rl: RateLimiter):
            label = e.get("label", "")
            uri = e.get("uri", "")
            safe = _safe_name(label)
            
            iter3_hint_file = os.path.join(hints_dir, f"iter3_hints_{safe}.txt")
            iter3_2_done_marker = os.path.join(hints_dir, f"iter3_2_done_{safe}.marker")
            
            # Skip if iter3_2 already completed or iter3 hints don't exist
            if os.path.exists(iter3_2_done_marker):
                print(f"⏭️  Skip iter3_2 for '{label}': already enriched")
                return
            if not os.path.exists(iter3_hint_file):
                print(f"⚠️  Skip iter3_2 for '{label}': iter3 hints not found")
                return
            
            async with semaphore:
                await rl.wait()
                print(f"🔍 Running iter3_2 enrichment for entity '{label}'...")
                
                # Read current iter3 hints (should have iter3_1 enrichment already)
                iter3_hints = _read_text(iter3_hint_file)

                # Get pre-extraction text (required)
                llm_out_dir = os.path.join(doi_dir, "llm_based_results")
                entity_text_path = os.path.join(llm_out_dir, f"entity_text_{safe}.txt")
                pre_text = ""
                if os.path.exists(entity_text_path):
                    try:
                        pre_text = _read_text(entity_text_path)
                    except Exception:
                        pre_text = ""
                if not pre_text:
                    print(f"⚠️  Skip iter3_2 for '{label}': pre-extraction text not found")
                    return
                source_text = pre_text
                
                # Build enrichment prompt including current iter3 hints as reference
                enrichment_prompt = f"{scope_3_2}\n\nEntity: {label}\n\nCurrent Iter3 Hints (for reference):\n{iter3_hints}\n\nText:\n{source_text}"
                
                # Extract vessel types and additional details
                try:
                    vessel_enriched_hints = await extract_content(
                        paper_content=source_text,
                        goal=enrichment_prompt,
                        t_box="",
                        entity_label=None,
                        entity_uri=None,
                        previous_extraction=iter3_hints,
                        model_name=get_extraction_model("iter3_2_enrichment"),
                    )

                    # Overwrite iter3 hints with iter3_2 enriched results
                    _write_text(iter3_hint_file, vessel_enriched_hints)
                    # Create done marker
                    _write_text(iter3_2_done_marker, "done")
                    print(f"✅ Enriched iter3 hints for '{label}' with iter3_2 vessel types")
                except Exception as e:
                    print(f"❌ Failed iter3_2 enrichment for '{label}': {e}")
        
        # Build job list for iter3_2
        iter3_2_jobs: List[Dict[str, str]] = []
        for e in top_entities:
            label = e.get("label", "")
            safe = _safe_name(label)
            iter3_2_done_marker = os.path.join(hints_dir, f"iter3_2_done_{safe}.marker")
            iter3_hint_file = os.path.join(hints_dir, f"iter3_hints_{safe}.txt")
            
            if os.path.exists(iter3_2_done_marker):
                print(f"⏭️  Skip iter3_2 for '{label}': already enriched")
                continue
            if not os.path.exists(iter3_hint_file):
                print(f"⚠️  Skip iter3_2 for '{label}': iter3 hints not found")
                continue
            iter3_2_jobs.append(e)
        
        if iter3_2_jobs:
            print(f"🚦 Running iter3_2 enrichment for {len(iter3_2_jobs)} entities")
            sem_3_2 = asyncio.Semaphore(8)
            rate_3_2 = RateLimiter(1.0, 2.0)
            tasks_3_2 = [asyncio.create_task(_enrich_iter3_2_entity(e, sem_3_2, rate_3_2)) 
                         for e in iter3_2_jobs]
            await asyncio.gather(*tasks_3_2)
            print("✅ iter3_2 enrichment completed")
        else:
            print("⏭️  No entities require iter3_2 enrichment")

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
            instr += ("\n\n"
                      "Before exporting the final TTL/memory, call the tool `check_orphan_entities` to detect any orphan entities. "
                      "If any are found, attempt to connect them appropriately (e.g., attach to synthesis, steps, IO, or parameters). "
                      "If you cannot connect some, list their details in your response and proceed with export.")

            _write_text(os.path.join(hints_dir, f"iter{iter_no}_{safe}_instruction.md"),
                        f"# Iteration {iter_no} — {label} — Instruction\n\n{instr}")

            print(f"🚀 Running iteration {iter_no} for entity: {label}")
            # Write global state for this entity (include entity IRI)
            write_global_state(doi, safe, uri)
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
async def run_task_hints_only(doi: str, start_iter: int = 2, end_iter: int = 4, include_iter3_1: bool = True):
    """Generate ONLY hints for iterations in [start_iter, end_iter] and stop.

    - Requires iteration 1 to have been completed (for top entities discovery).
    - Does NOT run any per-entity execution step or iter3_1 enrichment.
    - Skips already existing hint files.
    """
    doi_dir = os.path.join("data", doi)
    md_path = os.path.join(doi_dir, f"{doi}_stitched.md")
    if not os.path.exists(md_path):
        print(f"Skipping {doi}: _stitched.md file not found")
        return

    paper = _read_text(md_path)
    t_box = _discover_tbox(md_path)

    scopes = collect_scopes(scopes_ns)
    if not scopes:
        print("No EXTRACTION_SCOPE_* found. Nothing to run.")
        return

    hints_dir = os.path.join(doi_dir, "mcp_run")
    os.makedirs(hints_dir, exist_ok=True)

    # Load top entities from iter1
    entities_path = os.path.join(hints_dir, "iter1_top_entities.json")
    top_entities: List[Dict[str, str]] = []
    if os.path.exists(entities_path):
        try:
            with open(entities_path, "r", encoding="utf-8") as f:
                top_entities = json.load(f)
        except Exception:
            top_entities = []
    if not top_entities:
        # Fallback: try to parse from output_top.ttl if available
        top_entities = parse_top_level_entities(doi, output_file="output_top.ttl") or []
    if not top_entities:
        print("⚠️  No top entities available; cannot generate iter≥2 hints.")
        return

    # Build jobs for the requested iteration range and run hint extraction only
    iters = [i for (i, _s) in scopes if start_iter <= i <= end_iter and i != 1]
    if not iters:
        print("Nothing to do: no iterations in requested range.")
        return

    async def _extract_entity(iter_no: int, scope_text: str, e: Dict[str, str],
                              semaphore: asyncio.Semaphore, rl: RateLimiter):
        label = e.get("label", "")
        uri = e.get("uri", "")
        safe = _safe_name(label)

        response_file = os.path.join(hints_dir, f"iter{iter_no}_{safe}.md")
        hint_file = os.path.join(hints_dir, f"iter{iter_no}_hints_{safe}.txt")
        if os.path.exists(hint_file):
            print(f"⏭️  Skip extraction iter {iter_no} for '{label}': {hint_file} exists")
            return

        async with semaphore:
            await rl.wait()
            try:
                # Choose source text: for iter3 use pre-extraction text if available
                source_text = paper
                if iter_no == 3:
                    llm_out_dir = os.path.join(doi_dir, "llm_based_results")
                    os.makedirs(llm_out_dir, exist_ok=True)
                    entity_text_path = os.path.join(llm_out_dir, f"entity_text_{safe}.txt")
                    
                    pre_text = ""
                    if os.path.exists(entity_text_path):
                        try:
                            pre_text = _read_text(entity_text_path)
                        except Exception:
                            pre_text = ""
                    
                    # If we have pre-text (cached), still save the prompt for comparison
                    if pre_text:
                        try:
                            pre_prompt = build_text_extraction_prompt(label, paper)
                            
                            # Get actual model name from config
                            model_name = get_extraction_model("iter3_pre_extraction")
                            
                            # Add model info at the end
                            pre_prompt_with_meta = f"""{pre_prompt}

---
EFFECTIVE MODEL CONFIGURATION (mcp_run - cached):
Model: {model_name}
Temperature: 0
Top_p: 1.0
Remote: True
Config source: configs/extraction_models.json -> iter3_pre_extraction
"""
                            
                            prompt_comparison_dir = os.path.join(doi_dir, "prompt_comparison", "mcp_run")
                            os.makedirs(prompt_comparison_dir, exist_ok=True)
                            prompt_comparison_path = os.path.join(prompt_comparison_dir, f"pre_extraction_prompt_{safe}.txt")
                            _write_text(prompt_comparison_path, pre_prompt_with_meta)
                        except Exception:
                            pass
                    
                    # If pre-extraction text doesn't exist, generate it
                    if not pre_text:
                        # Use the exact same pre-extraction logic as llm_based.py
                        try:
                            print(f"🔍 Running pre-extraction for entity '{label}' using llm_based.py logic...")
                            
                            # Build prompt using llm_based.py function (handles transformation detection internally)
                            pre_prompt = build_text_extraction_prompt(label, paper)
                            
                            # Get actual model name from config
                            model_name = get_extraction_model("iter3_pre_extraction")
                            print(f"📝 Using model from config: {model_name}")
                            
                            # Add model info at the end
                            pre_prompt_with_meta = f"""{pre_prompt}

---
EFFECTIVE MODEL CONFIGURATION (mcp_run):
Model: {model_name}
Temperature: 0
Top_p: 1.0
Remote: True
Config source: configs/extraction_models.json -> iter3_pre_extraction
"""
                            
                            # Save prompt for comparison
                            try:
                                prompt_comparison_dir = os.path.join(doi_dir, "prompt_comparison", "mcp_run")
                                os.makedirs(prompt_comparison_dir, exist_ok=True)
                                prompt_comparison_path = os.path.join(prompt_comparison_dir, f"pre_extraction_prompt_{safe}.txt")
                                _write_text(prompt_comparison_path, pre_prompt_with_meta)
                            except Exception:
                                pass
                            
                            # Create LLM instance with same config as llm_based.py
                            llm_pre = LLMCreator(
                                model=model_name,
                                model_config=ModelConfig(temperature=0, top_p=1.0),
                                remote_model=True,
                            ).setup_llm()
                            
                            # Extract with retries (matching llm_based.py)
                            for attempt in range(2):
                                try:
                                    resp = llm_pre.invoke(pre_prompt)
                                    pre_text = str(getattr(resp, "content", resp) or "").strip()
                                    if pre_text:
                                        break
                                except Exception as invoke_err:
                                    if attempt == 1:
                                        print(f"⚠️  Pre-extraction failed after retries: {invoke_err}")
                                        pre_text = ""
                            
                            if pre_text:
                                try:
                                    _write_text(entity_text_path, pre_text)
                                    print(f"✅ Saved pre-extraction text to {entity_text_path}")
                                except Exception as write_err:
                                    print(f"⚠️  Failed to write pre-extraction text: {write_err}")
                            else:
                                print(f"⚠️  Pre-extraction returned empty text for '{label}'")
                        except Exception as e:
                            print(f"⚠️  Pre-extraction setup failed for '{label}': {e}")
                            pre_text = ""
                    
                    if pre_text:
                        source_text = pre_text
                        print(f"✅ Using pre-extracted text for '{label}' ({len(pre_text)} chars)")
                    else:
                        print(f"⚠️  Using full paper for '{label}' (pre-extraction unavailable)")
                # Prepare prompt comparison path for iter3 and iter4
                save_prompt_path = None
                if iter_no in (3, 4):
                    prompt_comparison_dir = os.path.join(doi_dir, "prompt_comparison", "mcp_run")
                    os.makedirs(prompt_comparison_dir, exist_ok=True)
                    save_prompt_path = os.path.join(prompt_comparison_dir, f"iter{iter_no}_prompt_{safe}.txt")
                
                if iter_no == 3:
                    # Get actual model name from config
                    iter3_model = get_extraction_model("iter3_hints")
                    print(f"📝 Using iter3 model from config: {iter3_model}")
                    
                    # Format iter3 prompt to match llm_based.py exactly
                    # Format: [PROMPT]\n\nentity_label: <label>\ncontext:\n<<<\n[text]\n>>>\nOnly output...
                    formatted_goal = f"""{scope_text}

entity_label: {label}
context:
<<<
{source_text}
>>>
Only output the JSON object as specified. The MCP tool is a placeholder and does nothing; do not rely on tools.

---
EFFECTIVE MODEL CONFIGURATION (mcp_run):
Model: {iter3_model}
Temperature: 0
Top_p: 1
Remote: True
Config source: configs/extraction_models.json -> iter3_hints
"""
                    
                    hints = await extract_content(
                        paper_content="",  # Empty because we included it in formatted_goal
                        goal=formatted_goal,
                        t_box="",
                        entity_label=label,
                        entity_uri=uri,
                        model_name=iter3_model,
                        use_raw_prompt=True,
                        save_prompt_path=save_prompt_path,
                    )
                else:
                    hints = await extract_content(
                        paper_content=source_text,
                        goal=scope_text,
                        t_box=t_box,
                        entity_label=label or None,
                        entity_uri=uri or None,
                        model_name=get_extraction_model(f"iter{iter_no}_hints"),
                        use_raw_prompt=False,
                        save_prompt_path=save_prompt_path,
                    )
                _write_text(hint_file, hints)
                print(f"✅ Saved {hint_file}")
                if iter_no == 3:
                    extra_dir = os.path.join(doi_dir, "step_type_only_extraction")
                    os.makedirs(extra_dir, exist_ok=True)
                    extra_file = os.path.join(extra_dir, f"iter3_0_hints_{safe}.txt")
                    _write_text(extra_file, hints)
                    print(f"✅ Saved step-type-only copy to {extra_file}")
            except RuntimeError as e:
                print(f"❌ Failed to extract hints for iter {iter_no}, entity '{label}': {e}")

    for iter_no, scope_text in scopes:
        if iter_no not in iters:
            continue
        if not top_entities:
            print(f"⚠️  No top entities for iter {iter_no}.")
            continue

        jobs: List[Dict[str, str]] = []
        for e in top_entities:
            label = e.get("label", "")
            safe = _safe_name(label)
            hint_file = os.path.join(hints_dir, f"iter{iter_no}_hints_{safe}.txt")
            if os.path.exists(hint_file):
                print(f"⏭️  Skip extraction iter {iter_no} for '{label}': {hint_file} exists")
                continue
            jobs.append(e)

        if not jobs:
            continue

        print(f"🚦 Parallel extraction iter {iter_no} (hints only): {len(jobs)} entity jobs")
        sem = asyncio.Semaphore(8)
        rate = RateLimiter(1.0, 2.0)
        tasks = [asyncio.create_task(_extract_entity(iter_no, scope_text, e, sem, rate)) for e in jobs]
        await asyncio.gather(*tasks)

    # Optionally run iter3_1 enrichment after iter3 hints are available
    if include_iter3_1 and any(i == 3 for i in iters):
        scope_3_1 = getattr(scopes_ns, "EXTRACTION_SCOPE_3_1", "")
        if scope_3_1:
            print("🔄 Running iter3_1 (detailed step enrichment)...")

            async def _enrich_iter3_entity(e: Dict[str, str], semaphore: asyncio.Semaphore, rl: RateLimiter):
                label = e.get("label", "")
                uri = e.get("uri", "")
                safe = _safe_name(label)

                iter3_hint_file = os.path.join(hints_dir, f"iter3_hints_{safe}.txt")
                iter3_1_done_marker = os.path.join(hints_dir, f"iter3_1_done_{safe}.marker")

                if os.path.exists(iter3_1_done_marker):
                    print(f"⏭️  Skip iter3_1 for '{label}': already enriched")
                    return
                if not os.path.exists(iter3_hint_file):
                    print(f"⚠️  Skip iter3_1 for '{label}': iter3 hints not found")
                    return

                async with semaphore:
                    await rl.wait()
                    print(f"🔍 Running iter3_1 enrichment for entity '{label}'...")

                    iter3_steps = _read_text(iter3_hint_file)

                    llm_out_dir = os.path.join(doi_dir, "llm_based_results")
                    entity_text_path = os.path.join(llm_out_dir, f"entity_text_{safe}.txt")
                    pre_text = ""
                    if os.path.exists(entity_text_path):
                        try:
                            pre_text = _read_text(entity_text_path)
                        except Exception:
                            pre_text = ""

                    if not pre_text:
                        print(f"⚠️  Skip iter3_1 for '{label}': pre-extraction text not found")
                        return
                    source_text = pre_text

                    enrichment_prompt = f"{scope_3_1}\n\nEntity: {label}\n\nIter3 Step Types (for guidance):\n{iter3_steps}\n\nText:\n{source_text}"

                    try:
                        detailed_hints = await extract_content(
                            paper_content=source_text,
                            goal=enrichment_prompt,
                            t_box="",
                            entity_label=None,
                            entity_uri=None,
                            previous_extraction=iter3_steps,
                            model_name=get_extraction_model("iter3_1_enrichment"),
                        )
                        _write_text(iter3_hint_file, detailed_hints)
                        _write_text(iter3_1_done_marker, "done")
                        print(f"✅ Enriched iter3 hints for '{label}' with iter3_1 details")
                    except Exception as e:
                        print(f"❌ Failed iter3_1 enrichment for '{label}': {e}")

            # Build enrichment jobs
            iter3_1_jobs: List[Dict[str, str]] = []
            for e in top_entities:
                label = e.get("label", "")
                safe = _safe_name(label)
                iter3_1_done_marker = os.path.join(hints_dir, f"iter3_1_done_{safe}.marker")
                iter3_hint_file = os.path.join(hints_dir, f"iter3_hints_{safe}.txt")
                if os.path.exists(iter3_1_done_marker):
                    print(f"⏭️  Skip iter3_1 for '{label}': already enriched")
                    continue
                if not os.path.exists(iter3_hint_file):
                    print(f"⚠️  Skip iter3_1 for '{label}': iter3 hints not found")
                    continue
                iter3_1_jobs.append(e)

            if iter3_1_jobs:
                # Backup original iter3_hints files before enrichment
                iter3_results_dir = os.path.join(hints_dir, "iter3_results")
                os.makedirs(iter3_results_dir, exist_ok=True)
                print(f"📦 Backing up original iter3_hints files to {iter3_results_dir}")
                for e in iter3_1_jobs:
                    label = e.get("label", "")
                    safe = _safe_name(label)
                    iter3_hint_file = os.path.join(hints_dir, f"iter3_hints_{safe}.txt")
                    if os.path.exists(iter3_hint_file):
                        backup_file = os.path.join(iter3_results_dir, f"iter3_hints_{safe}.txt")
                        shutil.copy2(iter3_hint_file, backup_file)
                        print(f"  ✓ Backed up iter3_hints_{safe}.txt")
                
                print(f"🚦 Running iter3_1 enrichment for {len(iter3_1_jobs)} entities")
                sem_3_1 = asyncio.Semaphore(8)
                rate_3_1 = RateLimiter(1.0, 2.0)
                tasks_3_1 = [asyncio.create_task(_enrich_iter3_entity(e, sem_3_1, rate_3_1)) for e in iter3_1_jobs]
                await asyncio.gather(*tasks_3_1)
                print("✅ iter3_1 enrichment completed")
            else:
                print("⏭️  No entities require iter3_1 enrichment")
        
        # After iter3_1, optionally run iter3_2 for vessel types enrichment
        scope_3_2 = getattr(scopes_ns, "EXTRACTION_SCOPE_3_2", "")
        if scope_3_2:
            print("🔄 Running iter3_2 (vessel type & equipment enrichment)...")

            async def _enrich_iter3_2_entity(e: Dict[str, str], semaphore: asyncio.Semaphore, rl: RateLimiter):
                label = e.get("label", "")
                uri = e.get("uri", "")
                safe = _safe_name(label)

                iter3_hint_file = os.path.join(hints_dir, f"iter3_hints_{safe}.txt")
                iter3_2_done_marker = os.path.join(hints_dir, f"iter3_2_done_{safe}.marker")

                if os.path.exists(iter3_2_done_marker):
                    print(f"⏭️  Skip iter3_2 for '{label}': already enriched")
                    return
                if not os.path.exists(iter3_hint_file):
                    print(f"⚠️  Skip iter3_2 for '{label}': iter3 hints not found")
                    return

                async with semaphore:
                    await rl.wait()
                    print(f"🔍 Running iter3_2 enrichment for entity '{label}'...")

                    iter3_hints = _read_text(iter3_hint_file)

                    llm_out_dir = os.path.join(doi_dir, "llm_based_results")
                    entity_text_path = os.path.join(llm_out_dir, f"entity_text_{safe}.txt")
                    pre_text = ""
                    if os.path.exists(entity_text_path):
                        try:
                            pre_text = _read_text(entity_text_path)
                        except Exception:
                            pre_text = ""

                    if not pre_text:
                        print(f"⚠️  Skip iter3_2 for '{label}': pre-extraction text not found")
                        return
                    source_text = pre_text

                    enrichment_prompt = f"{scope_3_2}\n\nEntity: {label}\n\nCurrent Iter3 Hints (for reference):\n{iter3_hints}\n\nText:\n{source_text}"

                    try:
                        vessel_enriched_hints = await extract_content(
                            paper_content=source_text,
                            goal=enrichment_prompt,
                            t_box="",
                            entity_label=None,
                            entity_uri=None,
                            previous_extraction=iter3_hints,
                            model_name=get_extraction_model("iter3_2_enrichment"),
                        )
                        _write_text(iter3_hint_file, vessel_enriched_hints)
                        _write_text(iter3_2_done_marker, "done")
                        print(f"✅ Enriched iter3 hints for '{label}' with iter3_2 vessel types")
                    except Exception as e:
                        print(f"❌ Failed iter3_2 enrichment for '{label}': {e}")

            # Build enrichment jobs
            iter3_2_jobs: List[Dict[str, str]] = []
            for e in top_entities:
                label = e.get("label", "")
                safe = _safe_name(label)
                iter3_2_done_marker = os.path.join(hints_dir, f"iter3_2_done_{safe}.marker")
                iter3_hint_file = os.path.join(hints_dir, f"iter3_hints_{safe}.txt")
                if os.path.exists(iter3_2_done_marker):
                    print(f"⏭️  Skip iter3_2 for '{label}': already enriched")
                    continue
                if not os.path.exists(iter3_hint_file):
                    print(f"⚠️  Skip iter3_2 for '{label}': iter3 hints not found")
                    continue
                iter3_2_jobs.append(e)

            if iter3_2_jobs:
                print(f"🚦 Running iter3_2 enrichment for {len(iter3_2_jobs)} entities")
                sem_3_2 = asyncio.Semaphore(8)
                rate_3_2 = RateLimiter(1.0, 2.0)
                tasks_3_2 = [asyncio.create_task(_enrich_iter3_2_entity(e, sem_3_2, rate_3_2)) for e in iter3_2_jobs]
                await asyncio.gather(*tasks_3_2)
                print("✅ iter3_2 enrichment completed")
            else:
                print("⏭️  No entities require iter3_2 enrichment")

    print("✅ Hints-only generation completed.")


async def run_task_iter1_only(doi: str, test: bool = False, test_iteration_num: int | None = None):
    """Run only iteration 1 for the given DOI folder and emit iteration_1.ttl.
    Also writes iter1_top_entities.json for downstream steps.
    """
    doi_dir = os.path.join("data", doi)
    md_path = os.path.join(doi_dir, f"{doi}_stitched.md")
    if not os.path.exists(md_path):
        print(f"Skipping {doi}: _stitched.md file not found")
        return

    paper = _read_text(md_path)
    t_box = _discover_tbox(md_path)

    scopes = collect_scopes(scopes_ns)
    prompt_map = collect_prompts(prompts_ns)
    if not scopes:
        print("No EXTRACTION_SCOPE_* found. Nothing to run.")
        return

    hints_dir = os.path.join(doi_dir, "mcp_run")
    os.makedirs(hints_dir, exist_ok=True)

    iter1_scope = next(((i, s) for (i, s) in scopes if i == 1), None)
    if not iter1_scope:
        print("⚠️  No iteration 1 scope found; nothing to do.")
        return

    i1, scope_text_1 = iter1_scope
    response_file_1 = os.path.join(hints_dir, "iter1_response.md")
    hint_file_1 = os.path.join(hints_dir, f"iter{i1}_hints.txt")

    # If test_iteration_num is provided, perform N separate runs for hints and top entities
    if isinstance(test_iteration_num, int) and test_iteration_num > 0:
        test_dir = os.path.join(doi_dir, "iter1_test_results")
        os.makedirs(test_dir, exist_ok=True)

        first_hints: str | None = None
        last_top_entities = []
        def _cleanup_between_runs():
            # Remove memory directories and TTL files under doi_dir
            try:
                for entry in os.listdir(doi_dir):
                    p = os.path.join(doi_dir, entry)
                    if os.path.isdir(p) and entry.startswith("memory"):
                        shutil.rmtree(p, ignore_errors=True)
                    elif os.path.isfile(p) and p.lower().endswith(".ttl"):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
            except Exception:
                pass

        for i in range(1, test_iteration_num + 1):
            if i > 1:
                print("🧹 Cleaning memory and TTLs before next test run...")
                _cleanup_between_runs()
            print(f"🔍 Extracting hints for iteration {i1} (run {i})...")
            hints_i = await extract_content(
                paper_content=paper,
                goal=scope_text_1,
                t_box=t_box,
                entity_label=None,
                entity_uri=None,
                model_name=get_extraction_model("iter1_hints"),
            )
            if first_hints is None:
                first_hints = hints_i
            _write_text(os.path.join(test_dir, f"iter1_hints_{i}.txt"), hints_i)

            if i1 not in prompt_map:
                print("⚠️  No prompt for iteration 1; skipping agent execution for test run")
            else:
                instr_i = format_prompt(
                    prompt_map[i1],
                    doi=doi,
                    iteration=i1,
                    paper_content=hints_i,
                )
                print("🚀 Running iteration 1 agent (test run)...")
                write_global_state(doi, "top")
                agent1 = _agent(tools=["llm_created_mcp"])
                try:
                    resp1, _ = await _run_agent_with_retry(agent1, instr_i, max_retries=3, recursion_limit=600)
                    _write_text(response_file_1, f"# Iteration {i1} (test run {i})\n\n## Instruction\n\n{instr_i}\n\n## Response\n\n{resp1}")
                    out_local = os.path.join(doi_dir, "output.ttl")
                    top_local = os.path.join(doi_dir, "output_top.ttl")
                    if os.path.exists(out_local):
                        shutil.copy2(out_local, os.path.join(doi_dir, "iteration_1.ttl"))
                    elif os.path.exists(top_local):
                        shutil.copy2(top_local, os.path.join(doi_dir, "iteration_1.ttl"))
                except RuntimeError as e:
                    log.error(f"Failed to complete iteration 1 test run after retries: {e}")
                    print(f"❌ Failed to complete iteration 1 test run after retries: {e}")

            # Parse and write per-test top entities
            last_top_entities = parse_top_level_entities(doi, output_file="output_top.ttl") or []
            _write_text(os.path.join(test_dir, f"iter1_top_entities_{i}.json"), json.dumps(last_top_entities, indent=2))

        # Ensure baseline outputs exist (non-numbered)
        if not os.path.exists(hint_file_1) and first_hints is not None:
            _write_text(hint_file_1, first_hints)
            print(f"✅ Saved baseline hints to {hint_file_1}")
        _write_text(os.path.join(hints_dir, "iter1_top_entities.json"), json.dumps(last_top_entities, indent=2))
        print("✅ Iteration 1 test runs completed.")
        return
    else:
        # Default single-run behavior
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
                model_name=get_extraction_model("iter1_hints"),
            )
            _write_text(hint_file_1, hints1)
            print(f"✅ Saved hints to {hint_file_1}")

    if i1 not in prompt_map:
        print("⚠️  No prompt for iteration 1; stopping.")
        return

    instr1 = format_prompt(
        prompt_map[i1],
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
        write_global_state(doi, "top")
        agent1 = _agent(tools=["llm_created_mcp"])
        try:
            resp1, _ = await _run_agent_with_retry(agent1, instr1, max_retries=3, recursion_limit=600)
            _write_text(response_file_1,
                        f"# Iteration {i1}\n\n## Instruction\n\n{instr1}\n\n## Response\n\n{resp1}")
            out_local = os.path.join(doi_dir, "output.ttl")
            top_local = os.path.join(doi_dir, "output_top.ttl")
            if os.path.exists(out_local):
                shutil.copy2(out_local, iteration_1_ttl)
                print(f"✅ Saved iteration_1.ttl from output.ttl")
            elif os.path.exists(top_local):
                shutil.copy2(top_local, iteration_1_ttl)
                print(f"✅ Saved iteration_1.ttl from output_top.ttl")
            else:
                print("⚠️ No TTL produced for iteration 1 (neither output.ttl nor output_top.ttl found)")
        except RuntimeError as e:
            log.error(f"Failed to complete iteration 1 after retries: {e}")
            print(f"❌ Failed to complete iteration 1 after retries: {e}")

    # Always emit top entities JSON (if any)
    top_entities = parse_top_level_entities(doi, output_file="output_top.ttl") or []
    _write_text(os.path.join(hints_dir, "iter1_top_entities.json"), json.dumps(top_entities, indent=2))

    print("✅ Iteration 1 only flow completed.")

    return

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
