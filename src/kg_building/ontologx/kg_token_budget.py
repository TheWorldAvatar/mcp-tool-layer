"""Per-entity pipeline KG-building token budgets for fair OntoLogX comparison."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from paths import HERE, REPO_ROOT

from extraction_hints import HINT_RUNS
from src.extraction_runtime.names import entity_scope_name

MAIN_KG_TRACE_DIR = "main_kg_building"
OFFICIAL_KG_TRACE_DIRS = (
    "iter2_kg_building",
    "iter3_kg_building",
    "iter4_kg_building",
)
KG_TRACE_DIRS = OFFICIAL_KG_TRACE_DIRS + (MAIN_KG_TRACE_DIR,)
EXTENSION_TRACE_DIRS = {
    "ontospecies": ("ontospecies_kg_building", "extensions_kg_building"),
    "ontomops": ("ontomops_kg_building", "extensions_kg_building"),
}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _usage_tokens(payload: dict[str, Any]) -> tuple[int, int, int, int]:
    usage = payload.get("usage") or payload.get("token_usage") or {}
    prompt = _int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion = _int(usage.get("completion_tokens") or usage.get("output_tokens"))
    total = _int(usage.get("total_tokens")) or prompt + completion
    calls = _int(usage.get("calls"))
    return prompt, completion, total, calls


def _runtime_from_run(run: str, hash_id: str) -> Path:
    candidate = Path(run)
    if candidate.is_absolute() or any(sep in run for sep in ("/", "\\")):
        options = (
            candidate / "runtime" / hash_id,
            candidate / hash_id,
            candidate,
        )
        for runtime in options:
            if (runtime / "mcp_run").is_dir():
                return runtime
        return options[0]
    return REPO_ROOT / "scenarios" / "mops" / "runs" / run / "runtime" / hash_id


def runtime_for_hash(hash_id: str, run: str | None = None) -> tuple[Path, str]:
    if run:
        runtime = _runtime_from_run(run, hash_id)
        if not runtime.exists():
            raise FileNotFoundError(f"Missing hint-run runtime {runtime}")
        return runtime, run
    for candidate in HINT_RUNS:
        runtime = _runtime_from_run(candidate, hash_id)
        if (runtime / "mcp_run").exists():
            return runtime, candidate
    raise FileNotFoundError(f"No HINT_RUNS runtime for {hash_id}")


def _scope_for_label(runtime: Path, label: str) -> str | None:
    tops_path = runtime / "mcp_run" / "iter1_top_entities.json"
    if not tops_path.exists():
        return None
    records = json.loads(tops_path.read_text(encoding="utf-8"))
    for record in records:
        if str(record.get("label") or "").strip() == label.strip():
            return entity_scope_name(str(record.get("label") or ""), str(record.get("uri") or ""))
    return None


def _sum_traces(
    *,
    hash_id: str,
    label: str,
    runtime: Path,
    used_run: str,
    scope: str,
    iters: tuple[str, ...],
    include_continuity: bool,
) -> dict[str, Any]:
    prompt = completion = total = calls = 0
    files: list[str] = []
    by_dir: dict[str, int] = {}
    for folder_name in iters:
        folder = runtime / "responses" / folder_name
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob(f"{scope}*.trace.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            p_tok, c_tok, t_tok, n_calls = _usage_tokens(payload)
            prompt += p_tok
            completion += c_tok
            total += t_tok
            calls += n_calls
            try:
                files.append(str(path.relative_to(REPO_ROOT)))
            except ValueError:
                files.append(str(path))
            by_dir[folder_name] = by_dir.get(folder_name, 0) + t_tok

    if include_continuity:
        continuity = runtime / "responses" / "iteration_continuity" / f"{scope}.continuity_audit.json"
        if continuity.exists():
            payload = json.loads(continuity.read_text(encoding="utf-8"))
            p_tok, c_tok, t_tok, n_calls = _usage_tokens(payload)
            prompt += p_tok
            completion += c_tok
            total += t_tok
            calls += n_calls
            try:
                files.append(str(continuity.relative_to(REPO_ROOT)))
            except ValueError:
                files.append(str(continuity))
            by_dir["continuity"] = t_tok

    if total <= 0:
        raise FileNotFoundError(
            f"No KG-building token traces for {hash_id} / {label!r} ({scope}) in {used_run}"
        )
    return {
        "hash": hash_id,
        "label": label,
        "run": used_run,
        "scope": scope,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "llm_calls": calls,
        "trace_files": files,
        "by_dir": by_dir,
    }


def entity_kg_building_budget(
    hash_id: str,
    label: str,
    *,
    run: str | None = None,
    runtime: Path | None = None,
    iters: tuple[str, ...] | None = None,
    include_continuity: bool = False,
) -> dict[str, Any]:
    """Sum pipeline KG-building agent tokens for one ChemicalSynthesis.

    v2 one-shot KG writes ``responses/main_kg_building/{scope}.trace.json``.
    That spend already covers iter2+3+4 in one session, so it is preferred and
    not added to official ``iter2/3/4_kg_building`` traces. Presence / semantic
    / continuity judges are excluded by default.
    """
    if runtime is None:
        runtime, used_run = runtime_for_hash(hash_id, run)
    else:
        used_run = run or "test"
    scope = _scope_for_label(runtime, label)
    if not scope:
        raise FileNotFoundError(
            f"No iter1_top_entities.json scope for {hash_id} / {label!r} in {used_run}"
        )
    folders = KG_TRACE_DIRS if iters is None else iters
    kwargs = {
        "hash_id": hash_id,
        "label": label,
        "runtime": runtime,
        "used_run": used_run,
        "scope": scope,
        "include_continuity": include_continuity,
    }
    if MAIN_KG_TRACE_DIR in folders:
        try:
            return _sum_traces(**kwargs, iters=(MAIN_KG_TRACE_DIR,))
        except FileNotFoundError:
            rest = tuple(name for name in folders if name != MAIN_KG_TRACE_DIR)
            if not rest:
                raise
            return _sum_traces(**kwargs, iters=rest)
    return _sum_traces(**kwargs, iters=folders)


def entity_extension_budget(
    hash_id: str,
    label: str,
    *,
    domain: str = "ontospecies",
    run: str | None = None,
    runtime: Path | None = None,
) -> dict[str, Any] | None:
    """Sum pipeline extension KG-building tokens when traces exist."""
    folders = EXTENSION_TRACE_DIRS.get(domain, (f"{domain}_kg_building",))
    try:
        return entity_kg_building_budget(
            hash_id,
            label,
            run=run,
            runtime=runtime,
            iters=folders,
        )
    except FileNotFoundError:
        return None


def paper_kg_building_budget(hash_id: str, **kwargs: Any) -> dict[str, Any]:
    runtime, used_run = runtime_for_hash(hash_id, kwargs.get("run"))
    tops = json.loads((runtime / "mcp_run" / "iter1_top_entities.json").read_text(encoding="utf-8"))
    entities = [
        entity_kg_building_budget(hash_id, str(record["label"]), **kwargs) for record in tops
    ]
    return {
        "hash": hash_id,
        "run": used_run,
        "total_tokens": sum(item["total_tokens"] for item in entities),
        "entities": entities,
    }


__all__ = [
    "HINT_RUNS",
    "KG_TRACE_DIRS",
    "MAIN_KG_TRACE_DIR",
    "OFFICIAL_KG_TRACE_DIRS",
    "entity_kg_building_budget",
    "entity_extension_budget",
    "paper_kg_building_budget",
    "runtime_for_hash",
]
