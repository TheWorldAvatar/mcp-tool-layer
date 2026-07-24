"""
Pre-cache full-tier MOF competency atomic tools (uncapped SPARQL).

Use --comprehensive to warm every registered atomic (see atomic_warm_manifest.py),
not only atoms referenced in workflow JSON.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from mini_marie.mop_mof.mof.atomic_warm_manifest import workflow_driven_warm_specs
from mini_marie.mop_mof.mof.competency_cache import CompetencyCache, DEFAULT_ONLINE_LIMIT, warm_full_calls
from mini_marie.warm_manifest import specs_missing_full_tier
from mini_marie.mop_mof.mof.competency_workflow_engine import (
    collect_atomic_specs,
    load_manifest,
    load_workflow,
)


def collect_suite_warm_specs(workflow_ids: List[str] | None = None) -> List[Dict[str, Any]]:
    manifest = load_manifest()
    seen: set[str] = set()
    specs: List[Dict[str, Any]] = []
    for wf in manifest.get("workflows", []):
        wf_id = wf.get("id")
        if workflow_ids and wf_id not in workflow_ids:
            continue
        for spec in collect_atomic_specs(wf):
            key = json.dumps(spec, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            specs.append(spec)
    return specs


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-cache full-tier MOF competency atomics")
    parser.add_argument(
        "--comprehensive",
        action="store_true",
        help="Warm comprehensive atomics + all workflow arg variants (recommended)",
    )
    parser.add_argument("--workflow", action="append", help="Workflow id (non-comprehensive subset)")
    parser.add_argument("--suite", action="store_true", help="Warm atomics from all suite workflows")
    parser.add_argument("--tool", help="Single tool name")
    parser.add_argument("--args-json", default="{}", help="JSON object of tool args")
    parser.add_argument("--force", action="store_true", help="Re-fetch remote even if cached")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="With --comprehensive/--suite: warm only specs not yet in full-tier cache",
    )
    args = parser.parse_args()

    if args.comprehensive:
        specs = workflow_driven_warm_specs()
        label = "workflow_driven"
    elif args.tool:
        specs = [{"tool": args.tool, "args": json.loads(args.args_json)}]
        label = "single"
    elif args.suite:
        specs = collect_suite_warm_specs()
        label = "suite"
    elif args.workflow:
        specs = []
        for wf_id in args.workflow:
            specs.extend(collect_atomic_specs(load_workflow(wf_id)))
        seen: set[str] = set()
        unique: List[Dict[str, Any]] = []
        for spec in specs:
            key = json.dumps(spec, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique.append(spec)
        specs = unique
        label = "workflows"
    else:
        raise SystemExit("Provide --comprehensive, --tool, --workflow <id>, or --suite")

    if args.missing_only and not args.force:
        cache = CompetencyCache()
        try:
            before = len(specs)
            specs = specs_missing_full_tier(specs, has_full=cache.has_full)
            label = f"{label}_missing({len(specs)}/{before})"
        finally:
            cache.close()

    print(f"Warming {len(specs)} atomic spec(s) [{label}]...", flush=True)
    summary = warm_full_calls(specs, force=args.force)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
