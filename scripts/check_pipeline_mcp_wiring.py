#!/usr/bin/env python3
"""
Cheap sanity check: show which MCP module the KG construction pipeline will use.

This reads:
  - configs/meta_task/meta_task_config.json (main ontology)
  - the referenced MCP set JSON under configs/ (e.g., configs/run_created_mcp.json)

Then it prints:
  - mcp_set_name + mcp_list
  - resolved python -m module for each MCP tool key
  - whether that module is import-discoverable (importlib find_spec)

No server is started; no LLM calls are made.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_module(entry: dict) -> str | None:
    args = entry.get("args") or []
    if isinstance(args, list):
        for i, a in enumerate(args):
            if a == "-m" and i + 1 < len(args):
                return str(args[i + 1])
    return None


def main() -> int:
    # When running as a script (python scripts/...), sys.path[0] becomes ".../scripts".
    # Add repo root so "-m ai_generated_contents_candidate..." resolution matches pipeline behavior.
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    ap = argparse.ArgumentParser(description="Check effective pipeline MCP wiring (no generation).")
    ap.add_argument("--meta-config", default="configs/meta_task/meta_task_config.json")
    ap.add_argument("--configs-dir", default="configs")
    ap.add_argument("--mcp-name", default="llm_created_mcp", help="MCP key used by pipeline (default: llm_created_mcp)")
    args = ap.parse_args()

    meta_path = Path(args.meta_config)
    cfg_dir = Path(args.configs_dir)
    meta = _load_json(meta_path)
    main_onto = meta.get("ontologies", {}).get("main", {}) or {}
    mcp_set_name = main_onto.get("mcp_set_name", "run_created_mcp.json")
    mcp_list = main_onto.get("mcp_list", ["llm_created_mcp"])

    set_path = cfg_dir / mcp_set_name
    mcp_set = _load_json(set_path)

    print(f"[META] {meta_path.as_posix()}")
    print(f"  ontology={main_onto.get('name')}")
    print(f"  mcp_set_name={mcp_set_name}")
    print(f"  mcp_list={mcp_list}")
    print()
    print(f"[MCP SET] {set_path.as_posix()}")

    for key in mcp_list:
        entry = mcp_set.get(key) or {}
        module = _resolve_module(entry)
        ok = False
        err = None
        if module:
            try:
                ok = bool(importlib.util.find_spec(module))
            except Exception as e:
                ok = False
                err = str(e)
        line = f"  - {key}: module={module!r} importable={ok}"
        if err:
            line += f" error={err!r}"
        print(line)

    # Convenience: explicitly check llm_created_mcp
    if args.mcp_name not in mcp_list:
        entry = mcp_set.get(args.mcp_name) or {}
        module = _resolve_module(entry)
        ok = bool(module and importlib.util.find_spec(module))
        print()
        print(f"[NOTE] {args.mcp_name} not in mcp_list; current set entry is: module={module!r} importable={ok}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


