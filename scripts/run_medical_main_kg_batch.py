"""
Run `src.pipelines.main_kg_building.build.run_step` for one or more medical DOI hashes.

Typical use (same settings as a one-off `python -c` run):

  python scripts/run_medical_main_kg_batch.py \\
    --data-dir data_medical_e2e_json_full \\
    --meta-task-config configs/meta_task/meta_task_config_medical_non_flat_v3.json \\
    --hashes ce49a454 4dd7b3a0 eb7ead0d d2b47254

Prerequisites per hash under --data-dir: at least `mcp_run/iter1_top_entities.json`
and the rest of the pipeline state expected by main KG building
(see `src/pipelines/main_kg_building/build.py::run_step`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Batch main_kg_building run_step for medical hashes.")
    p.add_argument(
        "--data-dir",
        default="data_medical_e2e_json_full",
        help="Pipeline data directory (default: data_medical_e2e_json_full).",
    )
    p.add_argument(
        "--project-root",
        default=".",
        help="Repository root for resolving ai_generated_contents paths (default: .).",
    )
    p.add_argument(
        "--meta-task-config",
        default="configs/meta_task/meta_task_config_medical_non_flat_v3.json",
        help="Meta task config path (default: medical non-flat v3).",
    )
    p.add_argument(
        "--hashes",
        nargs="*",
        default=[],
        help="8-char DOI hash folders to run. If omitted, use --op-bericht-all.",
    )
    p.add_argument(
        "--op-bericht-all",
        action="store_true",
        help="Run OP Bericht 1–5 hashes from data_agentic_medical_pipeline_valid/doi_to_hash.json.",
    )
    p.add_argument(
        "--doi-hash-file",
        default=None,
        help=(
            "Optional doi_to_hash.json path (default: <project-root>/data_agentic_medical_pipeline_valid/doi_to_hash.json)."
        ),
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    from src.pipelines.main_kg_building.build import run_step

    hashes: list[str] = list(args.hashes)
    if args.op_bericht_all:
        root = Path(args.project_root)
        doi_map_path = Path(args.doi_hash_file) if args.doi_hash_file else root / "data_agentic_medical_pipeline_valid" / "doi_to_hash.json"
        if not doi_map_path.exists():
            print(f"❌ Could not find doi_to_hash.json at {doi_map_path}", file=sys.stderr)
            return 2
        mapping = json.loads(doi_map_path.read_text(encoding="utf-8"))
        op_keys = ("OP Bericht 1", "OP Bericht 2", "OP Bericht 3", "OP Bericht 4", "OP Bericht 5")
        hashes = [str(mapping[k]) for k in op_keys if k in mapping]
        print(f"[INFO] OP Bericht hashes from {doi_map_path}: {hashes}")

    if not hashes:
        print(
            "❌ No hashes: pass positional --hashes or use --op-bericht-all.\n"
            "Example other four (after ec5d5219):\n"
            "  --hashes ce49a454 4dd7b3a0 eb7ead0d d2b47254",
            file=sys.stderr,
        )
        return 2

    cfg = {
        "data_dir": args.data_dir,
        "project_root": args.project_root,
        "meta_task_config": args.meta_task_config,
    }

    results: list[tuple[str, bool]] = []
    for h in hashes:
        ok = bool(run_step(h, cfg))
        results.append((h, ok))
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {h}")

    failed = [h for h, ok in results if not ok]
    if failed:
        print(f"\n❌ {len(failed)} run(s) failed: {', '.join(failed)}", file=sys.stderr)
        return 1
    print(f"\n✅ All {len(results)} run(s) succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
