#!/usr/bin/env python3
import os
import json
import argparse
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig

from src.agents.mops.dynamic_mcp.prompts.extraction_scopes import EXTRACTION_SCOPE_1, EXTRACTION_SCOPE_2
from src.agents.mops.dynamic_mcp.modules.extraction import (
    EXTRACTION_PROMPT,
    FOCUS_BLOCK_WITH_ENTITY,
    FOCUS_BLOCK_GLOBAL,
)

DATA_DIR = Path("data")
ONTO_DIR = DATA_DIR / "ontologies"
DOI_HASH_MAP_PATH = DATA_DIR / "doi_to_hash.json"

TOOLS_HINT = """
TOOLS HINTS
- Use MCP tools (pubchem, enhanced_websearch, ccdc) to retrieve canonical identifiers (formula/SMILES/CCDC) for each input or output chemical when needed.
- Cross-check whether components plausibly relate to the provided MOP/CBU formulas.
- Prefer explicit mappings to subcomponents (e.g., V6O6, SO4, OCH3) when possible.
- Keep output strictly to the extraction scope; do not add RDF. Markdown only.
""".strip()


CCDC_TEST_PROMPT_TEMPLATE = """
Search for the CCDC deposition number for the MOP compound named "{mop_name}".

Use the CCDC search tools to find the CCDC number. Use the search_ccdc_by_mop_name tool to search the CCDC by compound name. Report:
1. What tool you used
2. What input parameters you provided
3. What output/results you received
4. The CCDC number if found
""".strip()


def _load_doi_to_hash() -> Dict[str, str]:
    if not DOI_HASH_MAP_PATH.exists():
        return {}
    with open(DOI_HASH_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _invert_hash_to_doi(doi_to_hash: Dict[str, str]) -> Dict[str, str]:
    return {h: d for d, h in doi_to_hash.items()}
 

def _scope_with_tools_only(scope_text: str) -> str:
    parts = [scope_text.strip(), "", TOOLS_HINT]
    return "\n".join(parts)


def _with_doi_context(scope_text: str, doi: str) -> str:
    parts = [scope_text.strip(), "", "CONTEXT (paper metadata)", f"- DOI: {doi}"]
    parts.append("- You may cross-reference product CCDC using MCP or web search if helpful. Do not output CCDC itself; only use it to validate mapping.")
    return "\n".join(parts)


def _safe_name(label: str) -> str:
    return (
        (label or "entity")
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace("?", "_")
        .replace("*", "_")
        .replace("<", "_")
        .replace(">", "_")
        .replace("|", "_")
        .replace('"', "_")
        .replace("'", "_")
    )


def _discover_tbox(md_path: Path) -> str:
    tbox_path = md_path.parent / "ontosynthesis.ttl"
    if tbox_path.exists():
        try:
            return tbox_path.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


def _resolve_pairs(arg_file: Optional[str], doi_to_hash: Dict[str, str]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    hash_to_doi = _invert_hash_to_doi(doi_to_hash)

    if not arg_file:
        for entry in DATA_DIR.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith('.') or entry.name in ["log", "ontologies", "third_party_repos", "__pycache__"]:
                continue
            h = entry.name
            doi = hash_to_doi.get(h, None)
            if doi:
                pairs.append((h, doi))
        return pairs

    maybe_hash_dir = DATA_DIR / arg_file
    if maybe_hash_dir.exists() and maybe_hash_dir.is_dir():
        doi = hash_to_doi.get(arg_file, None)
        if doi:
            pairs.append((arg_file, doi))
        else:
            pairs.append((arg_file, ""))
        return pairs

    h = doi_to_hash.get(arg_file, None)
    if h:
        pairs.append((h, arg_file))
        return pairs

    print(f"Target '{arg_file}' not found as hash or DOI; nothing to do.")
    return pairs


async def _extract_for_entity(agent: BaseAgent, hash_id: str, entity: Dict, prompt_text: str, t_box_text: str, md_text: str, out_dir: Path) -> None:
    label = entity.get("label", "")
    uri = entity.get("uri", "")

    focus_block = (
        FOCUS_BLOCK_WITH_ENTITY.format(entity_label=label or "", entity_uri=uri or "")
        if label and uri else
        FOCUS_BLOCK_GLOBAL
    )

    prompt = EXTRACTION_PROMPT.format(
        focus_block=focus_block,
        goal=prompt_text,
        paper_content=md_text or "",
        t_box=t_box_text or "",
    )

    safe = _safe_name(label)
    # Persist full prompt for transparency/debugging
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = out_dir / f"iter2_prompt_{safe}.md"
        with open(prompt_file, "w", encoding="utf-8") as pf:
            pf.write(prompt)
    except Exception:
        pass
    out_file = out_dir / f"iter2_hints_{safe}.txt"
    if out_file.exists():
        print(f"Skip: {hash_id} entity '{label}' (exists)")
        return

    print(f"Extracting (MCP): {hash_id} entity '{label}' ...")
    # Retry up to 3 times if file is not generated or empty
    max_retries = 3
    attempt = 0
    while attempt < max_retries:
        attempt += 1
        try:
            resp, _meta = await agent.run(prompt, recursion_limit=600)
            out_dir.mkdir(parents=True, exist_ok=True)
            content = str(resp or "")
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                size = out_file.stat().st_size
            except Exception:
                size = 0
            if size > 0:
                print(f"Wrote: {out_file}")
                return
            else:
                print(f"Attempt {attempt}: empty output for '{label}', retrying...")
        except Exception as e:
            print(f"Attempt {attempt} failed for entity '{label}' in {hash_id}: {e}")
        await asyncio.sleep(2)

    print(f"Failed to generate result for '{label}' in {hash_id} after {max_retries} attempts")


async def _run_ccdc_test_for_entity(agent: BaseAgent, hash_id: str, entity: Dict, out_dir: Path) -> None:
    """Run CCDC test for a single entity, mimicking extraction flow but with test prompt."""
    label = entity.get("label", "")
    
    if not label:
        print(f"Skip: entity has no label")
        return
    
    # Use the simple CCDC test prompt instead of full extraction prompt
    instruction = CCDC_TEST_PROMPT_TEMPLATE.format(mop_name=label)
    
    safe = _safe_name(label)
    # Persist prompt for transparency (same pattern as normal mode)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = out_dir / f"iter2_prompt_{safe}.md"
        with open(prompt_file, "w", encoding="utf-8") as pf:
            pf.write(instruction)
    except Exception:
        pass
    # Match normal mode filename pattern
    out_file = out_dir / f"iter2_hints_{safe}.txt"
    
    if out_file.exists():
        print(f"Skip: {hash_id} entity '{label}' (exists)")
        return
    
    print(f"CCDC Test (MCP): {hash_id} entity '{label}' ...")
    
    # Match normal mode retries and recursion limit
    max_retries = 3
    attempt = 0
    while attempt < max_retries:
        attempt += 1
        try:
            resp, meta = await agent.run(instruction, recursion_limit=600)
            out_dir.mkdir(parents=True, exist_ok=True)
            content = str(resp or "")
            
            # Write output
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                size = out_file.stat().st_size
            except Exception:
                size = 0
            if size > 0:
                print(f"Wrote: {out_file}")
                # Show summary
                aggregated = meta.get("aggregated_usage", {}) if isinstance(meta, dict) else {}
                print(f"  → Tokens: {aggregated.get('total_tokens', 'n/a')}, Cost: ${aggregated.get('total_cost_usd', 0):.6f}")
                return
            else:
                print(f"Attempt {attempt}: empty output for '{label}', retrying...")
        except Exception as e:
            print(f"Attempt {attempt} failed for entity '{label}' in {hash_id}: {e}")
        
        import asyncio as _asyncio
        await _asyncio.sleep(2)
    
    print(f"Failed to generate result for '{label}' in {hash_id} after {max_retries} attempts")


async def _run_ccdc_test(hash_or_mop: str, doi_to_hash: Dict[str, str]) -> None:
    """Run standalone CCDC search test for entities from a hash or direct MOP name.
    Uses the EXACT SAME agent configuration as the normal extraction mode.
    
    Args:
        hash_or_mop: Either a DOI, a hash ID to load entities from, or a direct MOP name
        doi_to_hash: DOI to hash mapping for resolving hash IDs
    """
    # First, try to resolve DOI/hash consistently with normal mode
    pairs = _resolve_pairs(hash_or_mop, doi_to_hash)
    entities: list[dict] = []
    hash_id: str | None = None
    hash_dir: Path | None = None
    if pairs:
        # Use the first resolved hash
        hash_id, _doi = pairs[0]
        hash_dir = DATA_DIR / hash_id
        if hash_dir.exists() and hash_dir.is_dir():
            entities_path = hash_dir / "mcp_run" / "iter1_top_entities.json"
            if entities_path.exists():
                try:
                    with open(entities_path, "r", encoding="utf-8") as f:
                        entities = json.load(f)
                    print(f"Loaded {len(entities)} entities from {hash_id}")
                except Exception as e:
                    print(f"Warning: Could not load entities from {entities_path}: {e}")
    else:
        # Fall back: treat input as direct MOP name
        entities = [{"label": hash_or_mop, "uri": ""}]
        hash_id = "direct"
        print(f"Using direct MOP name: {hash_or_mop}")
    
    # Initialize agent with EXACT SAME config as normal extraction mode
    mcp_tools = ["pubchem", "enhanced_websearch", "ccdc"]
    model_config = ModelConfig(temperature=0, top_p=1)
    
    print("\n" + "=" * 80)
    print("CCDC MCP TEST MODE - Initializing Agent")
    print("=" * 80)
    print(f"    - Model: gpt-4.1")
    print(f"    - MCP Tools: {mcp_tools}")
    print(f"    - Temperature: {model_config.temperature}")
    print(f"    - Config: chemistry.json")
    
    agent = BaseAgent(
        model_name="gpt-4.1",
        model_config=model_config,
        remote_model=True,
        mcp_tools=mcp_tools,
        mcp_set_name="chemistry.json"
    )
    
    # Output directory: match normal mode when hash is present
    out_dir = (hash_dir / "mcp_run") if (hash_dir and hash_dir.exists()) else Path("data") / "ccdc_test_output"
    
    # Test each entity using the same flow style as normal mode
    print(f"\nTesting {len(entities)} entities...")
    for entity in entities:
        await _run_ccdc_test_for_entity(agent, hash_id or "direct", entity, out_dir)
    
    print("\n" + "=" * 80)
    print(f"ALL TESTS COMPLETED ({len(entities)} total)")
    print("=" * 80)


async def _merge_iter2_with_ccdc(iter2_text: str, ccdc_text: str) -> str:
    """Merge iter2 content with a CCDC number extracted from ccdc_text.
    Strategy: find a numeric CCDC (5-8 digits) in ccdc_text; update Representation line.
    If not found, append a brief note under ChemicalOutput.
    """
    # Try to find a plausible CCDC number
    m = re.search(r"\b(\d{5,8})\b", ccdc_text)
    ccdc_num = m.group(1) if m else None
    replacement_line = f"- Representation: {ccdc_num if ccdc_num else ''}"

    lines = iter2_text.splitlines()
    out_lines = []
    rep_replaced = False
    for ln in lines:
        if ln.strip().startswith("- Representation:") and not rep_replaced:
            out_lines.append(replacement_line)
            rep_replaced = True
        else:
            out_lines.append(ln)
    if not rep_replaced:
        # Try to insert under ChemicalOutput block
        inserted = False
        for i in range(len(out_lines)):
            if out_lines[i].strip().startswith("ChemicalOutput:"):
                out_lines.insert(i+1, replacement_line)
                inserted = True
                break
        if not inserted:
            out_lines.append("")
            out_lines.append("ChemicalOutput:")
            out_lines.append(replacement_line)

    # Clean output: no tool-call details; only update Representation
    return "\n".join(out_lines)


async def _run_iter2_1(hash_or_doi: Optional[str]) -> None:
    """Run iter2_1: read iter2 results and add CCDC only, writing iter2_1_hints_*.txt."""
    doi_to_hash = _load_doi_to_hash()
    pairs = _resolve_pairs(hash_or_doi, doi_to_hash) if hash_or_doi else []

    target_pairs: List[Tuple[str, str]]
    if pairs:
        target_pairs = pairs
    else:
        # process all hashes with iter2 files
        target_pairs = []
        hash_to_doi = _invert_hash_to_doi(doi_to_hash)
        for entry in DATA_DIR.iterdir():
            if entry.is_dir() and (entry / "mcp_run").exists():
                h = entry.name
                target_pairs.append((h, hash_to_doi.get(h, "")))

    # Prepare agent with the same config as normal mode
    mcp_tools = ["pubchem", "enhanced_websearch", "ccdc"]
    model_config = ModelConfig(temperature=0, top_p=1)
    agent = BaseAgent(model_name="gpt-4.1", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="chemistry.json")

    for hash_id, _doi in target_pairs:
        hash_dir = DATA_DIR / hash_id
        mcp_dir = hash_dir / "mcp_run"
        if not mcp_dir.exists():
            continue
        # find iter2 files
        iter2_files = sorted(p for p in mcp_dir.glob("iter2_hints_*.txt") if p.is_file())
        if not iter2_files:
            print(f"Skip {hash_id}: no iter2_hints_*.txt files found")
            continue
        print(f"iter2_1: processing {hash_id} with {len(iter2_files)} iter2 files")
        for f in iter2_files:
            label = f.stem.replace("iter2_hints_", "", 1)
            out_file = mcp_dir / f"iter2_hints_{label}.txt"
            # Overwrite existing iter2 hints with CCDC-enriched content
            try:
                iter2_text = f.read_text(encoding="utf-8")
            except Exception as e:
                print(f"Skip {hash_id}/{label}: cannot read iter2 file: {e}")
                continue

            instruction = CCDC_TEST_PROMPT_TEMPLATE.format(mop_name=label)
            print(f"iter2_1 CCDC (MCP): {hash_id} entity '{label}' ...")

            # Retry up to 3 times
            max_retries = 3
            attempt = 0
            ccdc_text = ""
            while attempt < max_retries:
                attempt += 1
                try:
                    resp, _meta = await agent.run(instruction, recursion_limit=600)
                    ccdc_text = str(resp or "")
                    if ccdc_text.strip():
                        break
                    else:
                        print(f"Attempt {attempt}: empty CCDC output for '{label}', retrying...")
                except Exception as e:
                    print(f"Attempt {attempt} failed for CCDC '{label}' in {hash_id}: {e}")
                import asyncio as _asyncio
                await _asyncio.sleep(2)

            merged = await _merge_iter2_with_ccdc(iter2_text, ccdc_text)
            try:
                mcp_dir.mkdir(parents=True, exist_ok=True)
                out_file.write_text(merged, encoding="utf-8")
                print(f"Wrote: {out_file}")
            except Exception as e:
                print(f"Failed to write iter2 (enriched) for {hash_id}/{label}: {e}")


async def run_test(arg_file: Optional[str] = None, ablation: bool = False, do_iter1: bool = False) -> None:
    doi_to_hash = _load_doi_to_hash()

    pairs = _resolve_pairs(arg_file, doi_to_hash)
    if not pairs:
        return

    # Configure MCP agent with pubchem + enhanced web search + ccdc (if available in config)
    mcp_tools = ["pubchem", "enhanced_websearch", "ccdc"]
    model_config = ModelConfig(temperature=0, top_p=1)
    agent = BaseAgent(model_name="gpt-4.1", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="chemistry.json")

    for hash_id, doi in pairs:
        hash_dir = DATA_DIR / hash_id
        md_path = hash_dir / f"{hash_id}_stitched.md"
        if not md_path.exists():
            print(f"Skip {hash_id}: stitched md not found")
            continue

        mcp_dir = hash_dir / "mcp_run"
        entities_path = mcp_dir / "iter1_top_entities.json"
        # Optionally run iteration 1 first (skip if already done)
        if do_iter1:
            iter1_hints = mcp_dir / "iter1_hints.txt"
            if iter1_hints.exists():
                print(f"Skip iter1: {hash_id} (exists)")
            else:
                # Build iter1 goal (scope-1) with DOI context
                iter1_goal = _with_doi_context(EXTRACTION_SCOPE_1, doi or "")
                focus_block = FOCUS_BLOCK_GLOBAL
                prompt_iter1 = EXTRACTION_PROMPT.format(
                    focus_block=focus_block,
                    goal=iter1_goal,
                    paper_content=md_path.read_text(encoding="utf-8") or "",
                    t_box=_discover_tbox(md_path) or "",
                )
                # Save full prompt
                try:
                    mcp_dir.mkdir(parents=True, exist_ok=True)
                    with open(mcp_dir / "iter1_prompt.md", "w", encoding="utf-8") as pf:
                        pf.write(prompt_iter1)
                except Exception:
                    pass
                # Run with retries
                print(f"Extracting (MCP iter1): {hash_id} ...")
                max_retries = 3
                attempt = 0
                while attempt < max_retries:
                    attempt += 1
                    try:
                        resp, _meta = await agent.run(prompt_iter1, recursion_limit=600)
                        content = str(resp or "")
                        with open(iter1_hints, "w", encoding="utf-8") as f:
                            f.write(content)
                        size = iter1_hints.stat().st_size if iter1_hints.exists() else 0
                        if size > 0:
                            print(f"Wrote: {iter1_hints}")
                            break
                        else:
                            print(f"Attempt {attempt}: empty iter1 output, retrying...")
                    except Exception as e:
                        print(f"Attempt {attempt} failed for iter1 {hash_id}: {e}")
                    await asyncio.sleep(2)

        if not entities_path.exists():
            print(f"Skip {hash_id}: iter1_top_entities.json not found")
            continue
        try:
            with open(entities_path, "r", encoding="utf-8") as f:
                top_entities = json.load(f)
        except Exception as e:
            print(f"Skip {hash_id}: cannot read top entities: {e}")
            continue
        if not top_entities:
            print(f"Skip {hash_id}: no top entities")
            continue

        # Always use tools-only scope (no MOP/CBU formulas in prompt)
        goal_text = _scope_with_tools_only(EXTRACTION_SCOPE_2)

        # add DOI context regardless of mode
        goal_text = _with_doi_context(goal_text, doi or "")

        t_box_text = _discover_tbox(md_path)
        md_text = md_path.read_text(encoding="utf-8")

        mode = "ABLATION" if ablation else "FORMULA"
        print(f"Running MCP iter2 test ({mode}) for {hash_id} (DOI: {doi or 'unknown'}) with {len(top_entities)} entities")
        for entity in top_entities:
            await _extract_for_entity(agent, hash_id, entity, goal_text, t_box_text, md_text, mcp_dir)


def main():
    parser = argparse.ArgumentParser(description="MCP Extraction Agent Test: iter2 hints with formulas and tools")
    parser.add_argument("--file", dest="file", type=str, help="Target by hash or DOI; default processes all. For --test/--iter2_1, this is the hash/DOI/MOP name", required=False)
    parser.add_argument("--ablation", action="store_true", help="Do not include MOP/CBU formulas in prompt; tools hint only")
    parser.add_argument("--iter1", action="store_true", help="Also run iteration 1 (skip if iter1_hints.txt exists)")
    parser.add_argument("--test", action="store_true", help="Run standalone CCDC search test. Use --file to specify hash (loads entities) or MOP name (direct search), defaults to IRMOP-51")
    parser.add_argument("--iter2_1", action="store_true", help="Run iter2_1: take iter2 results and add CCDC only, writing iter2_1_hints_*.txt")
    args = parser.parse_args()
    try:
        if args.iter2_1:
            import asyncio as _asyncio
            _asyncio.run(_run_iter2_1(args.file))
            return
        if args.test:
            # Test mode: load DOI mapping and run CCDC search
            doi_to_hash = _load_doi_to_hash()
            target = args.file if args.file else "IRMOP-51"
            print(f"Running CCDC test for: {target}")
            import asyncio as _asyncio
            _asyncio.run(_run_ccdc_test(target, doi_to_hash))
            return
        
        import asyncio as _asyncio
        # Normal extraction mode
        _asyncio.run(run_test(args.file, ablation=args.ablation, do_iter1=args.iter1))
    except KeyboardInterrupt:
        print("Interrupted")


if __name__ == "__main__":
    main()
