#!/usr/bin/env python3
"""
Rewire which generated ontology MCP module the pipeline uses (no regeneration).

This updates configs/run_created_mcp.json (and optionally configs/test_mcp_config.json)
to point llm_created_mcp at either:
  - ai_generated_contents_candidate.scripts.<ontology>.main   (candidate)
  - ai_generated_contents.scripts.<ontology>.main             (production)

Optionally updates configs/meta_task/meta_task_config.json to use the chosen mcp_set_name.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return bak


def _module_for(tree: str, ontology: str) -> str:
    if tree == "candidate":
        return f"ai_generated_contents_candidate.scripts.{ontology}.main"
    if tree == "production":
        # Safety: this repo does not always have production scripts as an importable package.
        # If ai_generated_contents/scripts/<ontology>/main.py doesn't exist, fail fast.
        prod_main = Path("ai_generated_contents") / "scripts" / ontology / "main.py"
        if not prod_main.exists():
            raise SystemExit(
                "Production MCP scripts are not present under ai_generated_contents/scripts/. "
                "Use --tree candidate (ai_generated_contents_candidate) or generate/promote scripts into production first."
            )
        return f"ai_generated_contents.scripts.{ontology}.main"
    raise ValueError(f"Unknown tree: {tree}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Rewire pipeline MCP module (no generation).")
    ap.add_argument("--ontology", default="ontosynthesis", help="Ontology name (e.g., ontosynthesis)")
    ap.add_argument("--tree", choices=["candidate", "production"], default="candidate", help="Which scripts tree to use")
    ap.add_argument(
        "--mcp-set",
        choices=["run_created_mcp.json", "test_mcp_config.json"],
        default="run_created_mcp.json",
        help="Which MCP set config the pipeline should use for llm_created_mcp",
    )
    ap.add_argument("--update-meta-task", action="store_true", help="Also update configs/meta_task/meta_task_config.json")
    ap.add_argument("--mcp-name", default="llm_created_mcp", help="Key name in the MCP set JSON")
    args = ap.parse_args()

    ontology = args.ontology.strip()
    if not ontology:
        raise SystemExit("ontology must be non-empty")

    module = _module_for(args.tree, ontology)

    cfg_dir = Path("configs")
    set_path = cfg_dir / args.mcp_set
    meta_path = cfg_dir / "meta_task" / "meta_task_config.json"

    # 1) Update the selected MCP set file
    _backup(set_path)
    cfg = _load_json(set_path)
    cfg.setdefault(args.mcp_name, {})
    cfg[args.mcp_name].update(
        {
            "command": "python",
            "args": ["-m", module],
            "transport": "stdio",
        }
    )
    _dump_json(set_path, cfg)

    # 2) Optionally update meta_task_config to reference the chosen set
    if args.update_meta_task:
        _backup(meta_path)
        meta = _load_json(meta_path)
        main_onto = meta.setdefault("ontologies", {}).setdefault("main", {})
        main_onto["mcp_set_name"] = args.mcp_set
        # keep mcp_list intact; ensure it includes the key
        mcp_list = main_onto.get("mcp_list") or []
        if args.mcp_name not in mcp_list:
            mcp_list = list(mcp_list) + [args.mcp_name]
        main_onto["mcp_list"] = mcp_list
        _dump_json(meta_path, meta)

    print(f"[OK] {set_path.as_posix()}: {args.mcp_name} -> {module}")
    if args.update_meta_task:
        print(f"[OK] {meta_path.as_posix()}: mcp_set_name -> {args.mcp_set}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


