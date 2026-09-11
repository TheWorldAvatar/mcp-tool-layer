"""Resolve generated prompts, iterations, SPARQL, and MCP scripts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from models.generated_layout import (
    generated_home,
    is_run_package,
    resolve_generated_package_root,
)
from models.locations import repository_root
from src.extraction_prompt_generation.slim_family_lock import (
    assert_slim_prompt_path,
)


def generated_artifact_root() -> Path:
    """Return the active generation batch, not the shared `generated/` home.

    Order: TWA_GENERATED_ARTIFACT_ROOT, generated/current.json, latest
    `generated/runs/*`, then the legacy flat `generated/` tree.
    """
    return resolve_generated_package_root()


def resolve_generated_file(path: str | Path) -> Path:
    """Resolve a generated artifact, including legacy `ai_generated_contents/` prefixes."""
    raw = str(path or "").replace("\\", "/").strip()
    if not raw:
        raise FileNotFoundError("generated artifact path is empty")
    root = generated_artifact_root()
    home = generated_home()
    strict = os.environ.get("TWA_REQUIRE_GENERATED_ARTIFACT_ROOT") == "1"
    isolated = strict or is_run_package(root, home) or root.resolve() != home.resolve()
    candidates: list[Path] = []

    def _after_prefix(value: str, prefixes: tuple[str, ...]) -> str:
        for prefix in prefixes:
            if value.startswith(prefix):
                return value[len(prefix) :].lstrip("/")
        return value

    relative = _after_prefix(
        raw,
        (
            "ai_generated_contents_candidate/",
            "ai_generated_contents/",
            "generated/",
        ),
    )
    as_path = Path(raw)
    if as_path.is_absolute():
        candidates.append(as_path)
    candidates.append(root / relative)
    if not isolated:
        repo = repository_root()
        candidates.append(repo / relative)
        candidates.append(repo / "generated" / relative)
        candidates.append(repo / "ai_generated_contents_candidate" / relative)
        candidates.append(repo / "ai_generated_contents" / relative)
        if as_path.exists():
            return as_path.resolve()

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file() or candidate.is_dir():
            return candidate.resolve()
    if strict:
        raise FileNotFoundError(f"Required generated artifact is missing: {candidates[0]}")
    return candidates[0]


def load_json(path: str | Path) -> dict[str, Any]:
    resolved = resolve_generated_file(path) if not Path(path).is_file() else Path(path)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_prompt(prompt_path: str | Path) -> str:
    """Load a generated prompt and splice a sibling `*.materializable.inc`."""
    resolved = resolve_generated_file(str(prompt_path))
    if not resolved.is_file():
        return ""
    prompt = resolved.read_text(encoding="utf-8")
    contract_path = resolved.with_name(f"{resolved.stem}.materializable.inc")
    if contract_path.is_file():
        component = contract_path.read_text(encoding="utf-8").strip()
        already_spliced = (
            "----- DETERMINISTIC T-BOX CONTRACT (mechanically spliced; do not edit) -----"
            in prompt
            or (component and component in prompt)
        )
        if not already_spliced and component:
            prompt = f"{prompt.rstrip()}\n\n{component}\n"
    assert_slim_prompt_path(resolved, prompt)
    return prompt


def prompt_contract_dependency_paths(prompt_path: str) -> list[str]:
    if not prompt_path:
        return []
    resolved = resolve_generated_file(prompt_path)
    return [str(prompt_path), str(resolved.with_name(f"{resolved.stem}.materializable.inc"))]


def iterations_config_path(ontology_name: str) -> Path:
    return resolve_generated_file(f"iterations/{ontology_name}/iterations.json")


def load_iterations_config(ontology_name: str) -> dict[str, Any]:
    path = iterations_config_path(ontology_name)
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def prompts_dir(ontology_name: str) -> Path:
    return resolve_generated_file(f"prompts/{ontology_name}")


def sparql_dir(ontology_name: str) -> Path:
    return resolve_generated_file(f"sparqls/{ontology_name}")


def scripts_dir(ontology_name: str) -> Path:
    return resolve_generated_file(f"scripts/{ontology_name}")


def generation_contract_path(ontology_name: str) -> Path:
    root = generated_artifact_root()
    for relative in (
        f"ontology_structures/{ontology_name}/generation_contract.json",
        f"semantic_planning/{ontology_name}/accepted_semantic_plan.json",
        f"{ontology_name}/generation_contract.json",
    ):
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return resolve_generated_file(
        f"ontology_structures/{ontology_name}/generation_contract.json"
    )


def load_generation_contract(ontology_name: str) -> dict[str, Any]:
    path = generation_contract_path(ontology_name)
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}
