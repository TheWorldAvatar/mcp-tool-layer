"""
Workflow-driven warm manifests: collect every (tool, args) from workflow JSON
and merge with comprehensive atomic specs (deduped).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence


def spec_key(spec: Dict[str, Any]) -> str:
    return json.dumps(spec, sort_keys=True, default=str)


def merge_warm_specs(*spec_lists: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dedupe {tool, args} specs preserving first occurrence order."""
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for specs in spec_lists:
        for spec in specs:
            if not spec.get("tool"):
                continue
            key = spec_key(spec)
            if key in seen:
                continue
            seen.add(key)
            out.append({"tool": spec["tool"], "args": dict(spec.get("args") or {})})
    return out


def seed_variables_from_workflow(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Scalars copied into variable resolution for warm spec collection."""
    seed: Dict[str, Any] = dict(workflow.get("seed_variables") or {})
    for key in (
        "city",
        "top_n",
        "usage_type",
        "min_height_m",
        "max_height_m",
        "min_height",
        "max_height",
        "usage_contains",
        "metal",
        "mof_name",
        "reference_name",
        "topology",
        "db_substring",
        "min_gsa",
    ):
        if workflow.get(key) is not None and key not in seed:
            seed[key] = workflow[key]
    return seed


def collect_atomic_specs_from_workflow(
    workflow: Dict[str, Any],
    *,
    resolve: Optional[Callable[[Any, Dict[str, Any]], Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Unique {tool, args} from tool steps (skips transform / local_join).

    Uses workflow seed scalars so $placeholders resolve the same as runtime.
    """
    variables = seed_variables_from_workflow(workflow)
    seen: set[str] = set()
    specs: List[Dict[str, Any]] = []
    for step in workflow.get("steps") or []:
        if step.get("type") in ("local_join", "transform", "aggregate"):
            continue
        if step.get("type") == "sparql":
            from mini_marie.sparql_utils import RESIDUAL_TOOL

            q = step.get("query")
            if isinstance(q, str) and q.strip() and "$" not in q:
                spec = {"tool": RESIDUAL_TOOL, "args": {"query": q.strip()}}
                key = spec_key(spec)
                if key not in seen:
                    seen.add(key)
                    specs.append(spec)
            continue
        tool = step.get("tool")
        if not tool:
            continue
        raw_args = step.get("args") or {}
        if resolve is not None:
            args = resolve(raw_args, variables)
        else:
            args = _shallow_resolve(raw_args, variables)
        spec = {"tool": tool, "args": args if isinstance(args, dict) else {}}
        key = spec_key(spec)
        if key in seen:
            continue
        seen.add(key)
        specs.append(spec)
    return specs


def collect_atomic_specs_from_workflows(
    workflows: Iterable[Dict[str, Any]],
    *,
    resolve: Optional[Callable[[Any, Dict[str, Any]], Any]] = None,
) -> List[Dict[str, Any]]:
    lists = [
        collect_atomic_specs_from_workflow(wf, resolve=resolve) for wf in workflows
    ]
    return merge_warm_specs(*lists)


def collect_atomic_specs_from_suite_manifest(
    manifest: Dict[str, Any],
    *,
    workflow_ids: Optional[List[str]] = None,
    resolve: Optional[Callable[[Any, Dict[str, Any]], Any]] = None,
) -> List[Dict[str, Any]]:
    workflows = manifest.get("workflows") or []
    if workflow_ids:
        workflows = [w for w in workflows if w.get("id") in workflow_ids]
    return collect_atomic_specs_from_workflows(workflows, resolve=resolve)


def collect_atomic_specs_from_workflow_dir(
    workflows_dir: Path,
    *,
    resolve: Optional[Callable[[Any, Dict[str, Any]], Any]] = None,
) -> List[Dict[str, Any]]:
    paths = sorted(workflows_dir.glob("*.json"))
    workflows: List[Dict[str, Any]] = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        workflows.append(data)
    return collect_atomic_specs_from_workflows(workflows, resolve=resolve)


def specs_missing_full_tier(
    specs: Sequence[Dict[str, Any]],
    *,
    has_full: Callable[[str, Dict[str, Any]], bool],
) -> List[Dict[str, Any]]:
    """Return specs with no full-tier cache entry yet."""
    missing: List[Dict[str, Any]] = []
    for spec in specs:
        tool = spec.get("tool")
        if not tool:
            continue
        args = spec.get("args") or {}
        if not has_full(tool, args):
            missing.append({"tool": tool, "args": dict(args)})
    return missing


def _shallow_resolve(value: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return variables.get(value[1:], value)
    if isinstance(value, list):
        return [_shallow_resolve(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: _shallow_resolve(v, variables) for k, v in value.items()}
    return value
