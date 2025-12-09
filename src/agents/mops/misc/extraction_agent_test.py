#!/usr/bin/env python3
import os
import json
import argparse
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.agents.mops.dynamic_mcp.prompts.extraction_scopes import EXTRACTION_SCOPE_2
from src.agents.mops.dynamic_mcp.modules.extraction import extract_content
from src.utils.extraction_models import get_extraction_model


DATA_DIR = Path("data")
ONTO_DIR = DATA_DIR / "ontologies"
MOP_CONCISE_PATH = ONTO_DIR / "full_mop_expanded_concise.json"
DOI_HASH_MAP_PATH = DATA_DIR / "doi_to_hash.json"


def _load_doi_to_hash() -> Dict[str, str]:
    if not DOI_HASH_MAP_PATH.exists():
        return {}
    with open(DOI_HASH_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _invert_hash_to_doi(doi_to_hash: Dict[str, str]) -> Dict[str, str]:
    return {h: d for d, h in doi_to_hash.items()}


def _load_mop_cbu_by_doi() -> Dict[str, Tuple[str, List[str]]]:
    """Return mapping: doi -> (mop_formula, [cbu_formula_1, cbu_formula_2, ...])"""
    if not MOP_CONCISE_PATH.exists():
        return {}
    try:
        with open(MOP_CONCISE_PATH, "r", encoding="utf-8") as f:
            records = json.load(f)
    except Exception:
        return {}

    result: Dict[str, Tuple[str, List[str]]] = {}
    for rec in records:
        doi = rec.get("doi")
        mop_formula = rec.get("mop_formula")
        cbus = rec.get("cbus", []) or []
        cbu_formulas: List[str] = []
        for c in cbus:
            ctd = (c or {}).get("cbu_formula")
            if ctd and ctd not in cbu_formulas:
                cbu_formulas.append(ctd)
        if doi and mop_formula:
            result[doi] = (mop_formula, cbu_formulas)
    return result


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


def _augment_scope_with_formulas(scope_text: str, mop_formula: Optional[str], cbu_formulas: List[str]) -> str:
    if not mop_formula and not cbu_formulas:
        return scope_text
    lines = [scope_text.strip(), "", "CONTEXT FORMULAS (for disambiguation)"]
    if mop_formula:
        lines.append(f"- MOP formula: {mop_formula}")
    if cbu_formulas:
        # Include up to 2 CBU formulas as requested
        take = cbu_formulas[:2]
        for idx, c in enumerate(take, start=1):
            lines.append(f"- CBU formula {idx}: {c}")
    return "\n".join(lines)


def _resolve_hash_and_doi(arg_file: Optional[str], doi_to_hash: Dict[str, str]) -> List[Tuple[str, str]]:
    """Return list of (hash, doi) pairs to process.
    - If arg_file is None: return all
    - If arg_file matches a hash dir in data: resolve doi via inverse map (if possible)
    - Else if arg_file matches a doi in mapping: resolve hash
    """
    pairs: List[Tuple[str, str]] = []
    hash_to_doi = _invert_hash_to_doi(doi_to_hash)

    if not arg_file:
        # all hashes under data, excluding special dirs
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

    # single target
    # case 1: looks like a directory hash
    maybe_hash_dir = DATA_DIR / arg_file
    if maybe_hash_dir.exists() and maybe_hash_dir.is_dir():
        doi = hash_to_doi.get(arg_file, None)
        if doi:
            pairs.append((arg_file, doi))
        else:
            # cannot resolve doi; still allow run with empty formulas
            pairs.append((arg_file, ""))
        return pairs

    # case 2: a DOI in mapping
    h = doi_to_hash.get(arg_file, None)
    if h:
        pairs.append((h, arg_file))
        return pairs

    print(f"Target '{arg_file}' not found as hash or DOI; nothing to do.")
    return pairs


async def _extract_for_entity(hash_id: str, entity: Dict, goal_text: str, t_box_text: str, md_text: str, out_dir: Path) -> None:
    label = entity.get("label", "")
    uri = entity.get("uri", "")
    safe = _safe_name(label)
    out_file = out_dir / f"iter2_hints_{safe}.txt"
    if out_file.exists():
        print(f"Skip: {hash_id} entity '{label}' (exists)")
        return

    print(f"Extracting: {hash_id} entity '{label}' ...")
    try:
        hints = await extract_content(
            paper_content=md_text,
            goal=goal_text,
            t_box=t_box_text,
            entity_label=label,
            entity_uri=uri,
            model_name=get_extraction_model("iter2_hints"),
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(hints)
        print(f"Wrote: {out_file}")
    except Exception as e:
        print(f"Error extracting for entity '{label}' in {hash_id}: {e}")


async def run_test(arg_file: Optional[str] = None) -> None:
    doi_to_hash = _load_doi_to_hash()
    doi_to_formulas = _load_mop_cbu_by_doi()

    pairs = _resolve_hash_and_doi(arg_file, doi_to_hash)
    if not pairs:
        return

    for hash_id, doi in pairs:
        hash_dir = DATA_DIR / hash_id
        md_path = hash_dir / f"{hash_id}_stitched.md"
        if not md_path.exists():
            print(f"Skip {hash_id}: stitched md not found")
            continue

        # Load iter1 entities
        mcp_dir = hash_dir / "mcp_run"
        entities_path = mcp_dir / "iter1_top_entities.json"
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

        # Prepare scope with formulas
        mop_formula: Optional[str] = None
        cbu_formulas: List[str] = []
        if doi and doi in doi_to_formulas:
            mop_formula, cbu_formulas = doi_to_formulas[doi]
        goal_text = _augment_scope_with_formulas(EXTRACTION_SCOPE_2, mop_formula, cbu_formulas)

        # Load context
        t_box_text = _discover_tbox(md_path)
        md_text = md_path.read_text(encoding="utf-8")

        print(f"Running iter2 test for {hash_id} (DOI: {doi or 'unknown'}) with {len(top_entities)} entities")
        tasks = []
        for entity in top_entities:
            tasks.append(_extract_for_entity(hash_id, entity, goal_text, t_box_text, md_text, mcp_dir))

        # Run sequentially to avoid rate limits; adjust to gather for parallel if desired
        for t in tasks:
            await t


def main():
    parser = argparse.ArgumentParser(description="Extraction Agent Test: iter2 hints with formulas")
    parser.add_argument("--file", dest="file", type=str, help="Target by hash or DOI; default processes all", required=False)
    args = parser.parse_args()

    try:
        asyncio.run(run_test(args.file))
    except KeyboardInterrupt:
        print("Interrupted")


if __name__ == "__main__":
    main()
