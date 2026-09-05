"""Compile extension enrichment-target SPARQL from a declared property path.

The only human input is the hop list of property local names (or IRIs) plus the
already-planned target class. The SELECT text is assembled with absolute IRIs
and no ontology-specific prefixes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from src.agents.scripts_and_prompts_generation.ttl_parser import parse_ontology_ttl


ENRICHMENT_TARGET_SPARQL_NAME = "enrichment_target.sparql"
ALLOWED_ENRICHMENT_TARGET_KEYS = frozenset(
    {
        "path",
        "root_variable",
        "target_variable",
        "cardinality",
    }
)


def validate_enrichment_target_declaration(raw: Any, *, prefix: str) -> dict[str, Any]:
    """Accept only the path declaration. Complete SPARQL is not a config field."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{prefix} must be an object")
    unknown = sorted(str(key) for key in raw if str(key) not in ALLOWED_ENRICHMENT_TARGET_KEYS)
    if unknown:
        raise ValueError(
            f"{prefix} contains non-allowlisted fields: " + ", ".join(unknown)
        )
    if "query_file" in raw or "query" in raw:
        raise ValueError(f"{prefix} must not contain handwritten SPARQL")
    path = [
        str(item).strip()
        for item in (raw.get("path") or [])
        if str(item).strip()
    ]
    if not path:
        raise ValueError(f"{prefix}.path must be a non-empty property hop list")
    root_variable = str(raw.get("root_variable") or "synthesis").strip() or "synthesis"
    target_variable = str(raw.get("target_variable") or "target").strip() or "target"
    cardinality = str(raw.get("cardinality") or "exactly_one").strip() or "exactly_one"
    if not _is_sparql_var_name(root_variable) or not _is_sparql_var_name(target_variable):
        raise ValueError(f"{prefix} variable names must be SPARQL local names")
    if root_variable == target_variable:
        raise ValueError(f"{prefix} root_variable and target_variable must differ")
    return {
        "path": path,
        "root_variable": root_variable,
        "target_variable": target_variable,
        "cardinality": cardinality,
    }


def _is_sparql_var_name(value: str) -> bool:
    return bool(value) and value[0].isalpha() and all(
        char.isalnum() or char == "_" for char in value
    )


def _is_absolute_iri(value: str) -> bool:
    return value.startswith(("http://", "https://", "urn:"))


def compile_enrichment_target_sparql(
    *,
    path_iris: list[str],
    target_class_iri: str,
    root_variable: str = "synthesis",
    target_variable: str = "target",
) -> str:
    """Render a path-shaped SELECT using only absolute IRIs."""
    hops = [str(item).strip() for item in path_iris if str(item).strip()]
    target_iri = str(target_class_iri or "").strip()
    if not hops:
        raise ValueError("enrichment target path must contain at least one property IRI")
    if not _is_absolute_iri(target_iri):
        raise ValueError("enrichment target class must be an absolute IRI")
    bad = [item for item in hops if not _is_absolute_iri(item)]
    if bad:
        raise ValueError(
            "enrichment target path IRIs must be absolute: " + ", ".join(bad)
        )
    if not _is_sparql_var_name(root_variable) or not _is_sparql_var_name(target_variable):
        raise ValueError("enrichment target variables must be SPARQL local names")

    lines = [f"SELECT DISTINCT ?{target_variable} WHERE {{"]
    subject = f"?{root_variable}"
    for index, predicate in enumerate(hops):
        obj = (
            f"?{target_variable}"
            if index == len(hops) - 1
            else f"?_n{index}"
        )
        lines.append(f"  {subject} <{predicate}> {obj} .")
        subject = obj
    lines.append(f"  ?{target_variable} a <{target_iri}> .")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def property_iri_lookup(*parsed_bundles: Mapping[str, Any] | None) -> dict[str, str]:
    """Map property local names to IRIs from one or more parsed T-Boxes."""
    lookup: dict[str, str] = {}
    for parsed in parsed_bundles:
        for local, spec in ((parsed or {}).get("properties") or {}).items():
            name = str(local or "").strip()
            iri = str((spec or {}).get("iri") or "").strip()
            if name and _is_absolute_iri(iri):
                lookup.setdefault(name, iri)
    return lookup


def resolve_declared_path_iris(
    path: list[str],
    *,
    lookup: Mapping[str, str],
) -> list[str]:
    """Accept either already-absolute IRIs or T-Box local names."""
    resolved: list[str] = []
    for hop in path:
        token = str(hop).strip()
        if _is_absolute_iri(token):
            resolved.append(token)
            continue
        iri = str(lookup.get(token) or "").strip()
        if not iri:
            raise ValueError(
                f"enrichment target hop {token!r} is absent from the T-Box inventory"
            )
        resolved.append(iri)
    return resolved


def parse_tbox_paths(paths: list[str | Path]) -> dict[str, Any]:
    merged: dict[str, Any] = {"properties": {}, "classes": {}}
    for raw in paths:
        path = Path(raw)
        if not path.is_file():
            continue
        parsed = parse_ontology_ttl(str(path))
        for key in ("properties", "classes"):
            bucket = merged.setdefault(key, {})
            for local, spec in (parsed.get(key) or {}).items():
                bucket.setdefault(local, spec)
    return merged


def tbox_paths_from_context(context: Any) -> list[Path]:
    """Collect primary, supporting, and upstream T-Box paths from a generation context."""
    found: list[Path] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        path = Path(text)
        key = str(path.resolve()) if path.exists() else text
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    bundle = getattr(context, "contract", {}).get("tbox_bundle") or {}
    primary = bundle.get("primary") or {}
    add(primary.get("resolved_path") or primary.get("configured_path"))
    for item in bundle.get("supporting") or []:
        if isinstance(item, Mapping):
            add(item.get("resolved_path") or item.get("configured_path"))
    ttl = getattr(getattr(context, "ontology", None), "ttl_file", "")
    add(ttl)
    return found


def declaration_from_domain_config(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    config_path = Path(path)
    if not config_path.is_file():
        return {}
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    runtime = raw.get("runtime") or {}
    if not isinstance(runtime, Mapping):
        return {}
    declared = runtime.get("enrichment_target")
    if not declared:
        return {}
    return validate_enrichment_target_declaration(
        declared, prefix="runtime.enrichment_target"
    )


def compile_from_context(context: Any) -> dict[str, Any] | None:
    """Compile one extension enrichment SPARQL when the domain declared a path."""
    provenance = getattr(context, "config_provenance", {}) or {}
    domain_path = (provenance.get("domain_config") or {}).get("path")
    declaration = declaration_from_domain_config(domain_path)
    if not declaration:
        return None
    if getattr(getattr(context, "ontology", None), "role", "") != "extension":
        raise ValueError(
            "runtime.enrichment_target is only valid on simple_extension domains"
        )
    focus = (getattr(context, "contract", {}) or {}).get("extension_focus") or {}
    target_class_iri = str(focus.get("class_iri") or "").strip()
    if not _is_absolute_iri(target_class_iri):
        raise ValueError(
            "extension enrichment SPARQL requires a planned extension_focus class IRI"
        )
    parsed = parse_tbox_paths(tbox_paths_from_context(context))
    path_iris = resolve_declared_path_iris(
        declaration["path"],
        lookup=property_iri_lookup(parsed, getattr(context, "parsed", None)),
    )
    query = compile_enrichment_target_sparql(
        path_iris=path_iris,
        target_class_iri=target_class_iri,
        root_variable=declaration["root_variable"],
        target_variable=declaration["target_variable"],
    )
    return {
        **declaration,
        "path_iris": path_iris,
        "target_class_iri": target_class_iri,
        "query": query,
        "filename": ENRICHMENT_TARGET_SPARQL_NAME,
    }


def write_enrichment_target_sparql(context: Any) -> str | None:
    """Write `sparqls/<ontology>/enrichment_target.sparql` when declared."""
    compiled = compile_from_context(context)
    if compiled is None:
        return None
    ontology = context.ontology.name
    sparql_dir = Path(context.output_root) / "sparqls" / ontology
    sparql_dir.mkdir(parents=True, exist_ok=True)
    path = sparql_dir / ENRICHMENT_TARGET_SPARQL_NAME
    path.write_text(compiled["query"], encoding="utf-8")
    _merge_policy_into_adapter(context, compiled)
    return str(path)


def _merge_policy_into_adapter(context: Any, compiled: Mapping[str, Any]) -> None:
    adapter = (
        Path(context.output_root)
        / "derived_inputs"
        / context.ontology.name
        / "meta_task_adapter.json"
    )
    if not adapter.is_file():
        return
    payload = json.loads(adapter.read_text(encoding="utf-8"))
    extensions = ((payload.get("ontologies") or {}).get("extensions") or [])
    policy = {
        "query_file": generated_enrichment_target_relative(context.ontology.name),
        "root_variable": compiled["root_variable"],
        "target_variable": compiled["target_variable"],
        "target_class_iri": compiled["target_class_iri"],
        "cardinality": compiled["cardinality"],
        "path": list(compiled["path"]),
    }
    for item in extensions:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "") != context.ontology.name:
            continue
        policies = dict(item.get("runtime_policies") or {})
        policies["enrichment_target"] = policy
        item["runtime_policies"] = policies
    adapter.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def generated_enrichment_target_relative(ontology_name: str) -> str:
    return f"sparqls/{ontology_name}/{ENRICHMENT_TARGET_SPARQL_NAME}"


def resolve_enrichment_target_sparql_path(
    ontology_name: str,
    *,
    query_file: str = "",
    project_root: str | Path = ".",
) -> Path:
    """Prefer the compiled package SPARQL, then a legacy query_file path."""
    relative = generated_enrichment_target_relative(ontology_name)
    roots: list[str] = []
    override = (
        os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "")
        .strip()
        .replace("\\", "/")
        .rstrip("/")
    )
    if override:
        roots.append(override)
    roots.extend(
        [
            "ai_generated_contents_candidate",
            "ai_generated_contents",
        ]
    )
    root = Path(project_root)
    for candidate_root in roots:
        path = root / candidate_root / relative
        if path.is_file():
            return path
    declared = str(query_file or "").strip()
    if declared:
        path = Path(declared)
        if not path.is_absolute():
            path = root / path
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"compiled enrichment-target SPARQL not found for {ontology_name}: {relative}"
    )
