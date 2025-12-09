#!/usr/bin/env python3
import os
import json
import argparse
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig

from src.agents.mops.cbu_derivation.integration import (
    _read_metal_cbu_pair,
    _read_organic_cbu_pair,
)

DATA_DIR = Path("data")
DOI_HASH_MAP_PATH = DATA_DIR / "doi_to_hash.json"


def _load_doi_to_hash() -> Dict[str, str]:
    if not DOI_HASH_MAP_PATH.exists():
        return {}
    with open(DOI_HASH_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _invert_hash_to_doi(doi_to_hash: Dict[str, str]) -> Dict[str, str]:
    return {h: d for d, h in doi_to_hash.items()}


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


def _load_top_entities(hash_id: str) -> List[Dict[str, str]]:
    p = DATA_DIR / hash_id / "mcp_run" / "iter1_top_entities.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def _first_json_block(txt: str) -> str:
    lines = txt.splitlines()
    in_block = False
    buf: List[str] = []
    fence_started = False
    for ln in lines:
        if not in_block:
            if ln.strip().startswith("```"):
                tag = ln.strip().lstrip("`").lower()
                if "json" in tag:  # json or jsonl
                    in_block = True
                    fence_started = True
                    continue
        else:
            if ln.strip().startswith("```"):
                break
            buf.append(ln)
    if fence_started:
        return "\n".join(buf)
    return txt


def _parse_iter2_hints(hash_id: str, entity_label: str) -> List[Dict[str, str]]:
    """Return list of chemical dicts with at least name and role from iter2 hints."""
    mcp_dir = DATA_DIR / hash_id / "mcp_run"
    if not mcp_dir.exists():
        return []
    safe = _safe_name(entity_label)
    candidates: List[Path] = []
    direct = mcp_dir / f"iter2_hints_{safe}.txt"
    if direct.exists():
        candidates.append(direct)
    else:
        # fallback: any file containing the label token
        for p in sorted(mcp_dir.glob("iter2_hints_*.txt")):
            if safe in p.name:
                candidates.append(p)
                break
    if not candidates:
        return []

    path = candidates[0]
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return []

    content = _first_json_block(raw)
    # Attempt to parse as JSONL stream of objects
    chems: List[Dict[str, str]] = []
    for part in content.split("}\n{"):
        s = part
        if not s.strip().startswith("{"):
            s = "{" + s
        if not s.strip().endswith("}"):
            s = s + "}"
        try:
            obj = json.loads(s)
            name = str(obj.get("name") or "").strip()
            role = str(obj.get("role") or "").strip()
            if name:
                chems.append({"name": name, "role": role})
        except Exception:
            pass
    if chems:
        return chems

    # Fallback: regex for '"name":' and optional '"role":'
    import re
    names = re.findall(r'"name"\s*:\s*"([^"]+)"', content)
    roles = re.findall(r'"role"\s*:\s*"([^"]+)"', content)
    out: List[Dict[str, str]] = []
    for i, nm in enumerate(names):
        out.append({"name": nm, "role": roles[i] if i < len(roles) else ""})
    return out


async def _query_ccdc(agent: BaseAgent, entity_label: str) -> str:
    """Use ccdc MCP to attempt fetching a CCDC number by entity label; return string or empty."""
    prompt = (
        "You have access to a CCDC MCP tool. Given a MOP product name, try to find its CCDC number.\n"
        "Return only the most likely CCDC number, or N/A if unknown.\n"
        f"Product: {entity_label}\n"
    )
    try:
        resp, _meta = await agent.run(prompt, recursion_limit=120)
        s = str(resp or "").strip()
        # heuristic: extract a numeric-looking CCDC id
        import re
        m = re.search(r"(\d{6,})", s)
        return m.group(1) if m else ("N/A" if s else "")
    except Exception:
        return ""


async def _write_summary_for_entity(agent: Optional[BaseAgent], hash_id: str, entity_label: str) -> None:
    out_dir = DATA_DIR / hash_id / "cbu_derivation" / "in_advance"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Read metal/organic CBU derived pairs
    metal = _read_metal_cbu_pair(hash_id, entity_label)
    organic = _read_organic_cbu_pair(hash_id, entity_label)

    # Ingredient hints from iter2
    hints = _parse_iter2_hints(hash_id, entity_label)

    ccdc_num = ""
    # If no known CCDC in structured data, try MCP ccdc
    if agent is not None:
        ccdc_num = await _query_ccdc(agent, entity_label)

    md_lines: List[str] = []
    md_lines.append(f"# {entity_label}")
    if ccdc_num:
        md_lines.append("")
        md_lines.append(f"CCDC: {ccdc_num}")
    md_lines.append("")
    md_lines.append("## Metal CBU")
    md_lines.append(f"- Formula: {metal.get('formula') or ''}")
    if metal.get("iri"):
        md_lines.append(f"- IRI: {metal.get('iri')}")
    md_lines.append("")
    md_lines.append("## Organic CBU")
    md_lines.append(f"- Formula: {organic.get('formula') or ''}")
    if organic.get("iri"):
        md_lines.append(f"- IRI: {organic.get('iri')}")
    md_lines.append("")
    md_lines.append("## Ingredient Hints")
    if hints:
        for h in hints:
            nm = h.get("name") or ""
            rl = h.get("role") or ""
            md_lines.append(f"- {nm}{f' ({rl})' if rl else ''}")
    else:
        md_lines.append("- N/A")

    out_path = out_dir / f"{_safe_name(entity_label)}.md"
    out_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"Wrote summary: {out_path}")


def _resolve_targets(arg_file: Optional[str]) -> List[Tuple[str, List[str]]]:
    """Return list of (hash, entity_labels). If no iter1 entities, return empty list for that hash."""
    doi_to_hash = _load_doi_to_hash()
    hash_to_doi = _invert_hash_to_doi(doi_to_hash)

    pairs: List[Tuple[str, List[str]]] = []

    def entities_for_hash(h: str) -> List[str]:
        ents = _load_top_entities(h)
        return [str(e.get("label") or "").strip() for e in ents if e.get("label")]

    if not arg_file:
        for entry in sorted(p for p in DATA_DIR.iterdir() if p.is_dir()):
            name = entry.name
            if name.startswith('.') or name in ["log", "ontologies", "third_party_repos", "__pycache__"]:
                continue
            pairs.append((name, entities_for_hash(name)))
        return pairs

    maybe_hash_dir = DATA_DIR / arg_file
    if maybe_hash_dir.exists() and maybe_hash_dir.is_dir():
        return [(arg_file, entities_for_hash(arg_file))]

    h = doi_to_hash.get(arg_file, None)
    if h:
        return [(h, entities_for_hash(h))]

    print(f"Target '{arg_file}' not found as hash or DOI; nothing to do.")
    return []


async def run(arg_file: Optional[str]) -> None:
    targets = _resolve_targets(arg_file)
    if not targets:
        return

    # Configure MCP ccdc tool agent
    model_config = ModelConfig(temperature=0, top_p=1)
    agent = BaseAgent(model_name="gpt-4.1", model_config=model_config, remote_model=True, mcp_tools=["ccdc"], mcp_set_name="extension.json")

    for h, entities in targets:
        if not entities:
            print(f"Skip {h}: no iter1 entities")
            continue
        print(f"Summarising CBUs in advance for {h} ({len(entities)} entities)")
        for label in entities:
            await _write_summary_for_entity(agent, h, label)


def main():
    ap = argparse.ArgumentParser(description="Create in-advance CBU derivation summary per entity as markdown")
    ap.add_argument("--file", dest="file", type=str, help="Target by hash or DOI; default all", required=False)
    args = ap.parse_args()
    try:
        asyncio.run(run(args.file))
    except KeyboardInterrupt:
        print("Interrupted")


if __name__ == "__main__":
    main()
