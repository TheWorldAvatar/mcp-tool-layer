#!/usr/bin/env python3
"""OntoSynthesis (main-only) semantic MCP closed loop with LLM repair proofs.

Outer loop: regenerate MCP from T-Box/contract → Level-1 repair → LLM full-T-Box
fixture → in-process materialize_hints → HermiT gate → sticky feedback →
LLM in-place semantic patch (non-trivial) → optional next regenerate.

Orchestrator code reads class/property locals from the generation contract /
parsed T-Box inventory (no hard-coded domain symbol literals).

``--prove-repairs`` injects non-trivial Level-1 (syntax) and Level-2 (unknown
property) defects and accepts a heal only when an **LLM unified-diff patch**
fixed the same package. No restore/undo/regenerate shortcuts count as proof.

Usage:
  python -m src.agents.scripts_and_prompts_generation.semantic_mcp_loop_ontosynthesis \\
    --generation-model gpt-5 --model gpt-5 --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import textwrap
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rdflib import Graph
from rdflib.namespace import RDF

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_prompt_slice,
    run_agentic_generation_experiment,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_llm_agents import (
    run_content_diagnosis_agent_sync,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    _import_generated_main_module,
    build_validation_report,
)
from src.agents.scripts_and_prompts_generation.content_fixture_score import (
    load_predicted_hints,
    score_graph_content,
    score_hint_content,
)
from src.agents.scripts_and_prompts_generation.content_diagnosis import (
    artifact_manifest,
    fixture_literals,
    json_digest,
    prompt_inventory,
    redact_diagnosis,
)
from src.agents.scripts_and_prompts_generation.structured_prompt_editor import (
    run_structured_prompt_editor,
)
from src.agents.scripts_and_prompts_generation.fix_package_structure import (
    create_init_files,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    autofix_ruff_on_scripts,
    invoke_json,
    level1_repair_loop,
    repair_python_file_with_llm_for_goal,
    run_ruff_on_scripts,
)
from src.pipelines.utils.hash import generate_hash

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_META_TASK = ROOT / "configs/meta_task/meta_task_config.json"
DEFAULT_TBOX_PATHS = [
    ROOT / "data/ontologies/ontosynthesis.ttl",
    ROOT / "data/ontologies/ontomops-subgraph.ttl",
    ROOT / "data/ontologies/ontospecies-subgraph.ttl",
    ROOT / "data/ontologies/om2.ttl",
]
DEFAULT_OUTPUT_ROOT = ROOT / "tmp" / "semantic_mcp_loop_ontosynthesis"
ONTOLOGY_NAME = "ontosynthesis"
LEVEL1_MARKER = "# LEVEL1_EXERCISE_FAIL"
# Intentionally not a T-Box local — used only for injected semantic-repair exercises.
SEMANTIC_POISON_PROP = "__SemanticLoopInjectedUnknownProp__"


def _entities_filename(ontology_name: str = ONTOLOGY_NAME) -> str:
    return f"{ontology_name}_creation_entities.py"


def _iri_local(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _top_entity_local(context: AgenticGenerationContext) -> str:
    return str(
        (context.contract.get("top_entity") or {}).get("class_local") or ""
    ).strip()


def _ordering_property_locals(context: AgenticGenerationContext) -> list[str]:
    profile = context.contract.get("ordered_member_profile") or {}
    return [
        str(x).strip()
        for x in (profile.get("single_valued_ordering_properties") or [])
        if str(x).strip()
    ]


def _primary_ordering_property(context: AgenticGenerationContext) -> str:
    props = _ordering_property_locals(context)
    if not props:
        raise ValueError(
            "Contract has no single_valued_ordering_properties; "
            "cannot run ordering-based semantic exercises."
        )
    return props[0]


def _class_ancestor_locals(
    classes: dict[str, Any], class_local: str, *, _seen: set[str] | None = None
) -> list[str]:
    seen = _seen if _seen is not None else set()
    if class_local in seen:
        return []
    seen.add(class_local)
    meta = classes.get(class_local) or {}
    out: list[str] = []
    for parent in meta.get("parent_classes") or []:
        local = _iri_local(parent)
        if not local:
            continue
        out.append(local)
        out.extend(_class_ancestor_locals(classes, local, _seen=seen))
    return out


def _normalized_symbol(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _predicate_target_stem(predicate_local: str) -> str:
    text = str(predicate_local or "").strip()
    for prefix in ("has", "is"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :]
    return text


def _top_level_materializable_classes(
    context: AgenticGenerationContext,
) -> set[str]:
    """Classes materialize_hints can attach under the top entity (from T-Box/contract)."""
    classes = context.parsed.get("classes") or {}
    class_locals = sorted(classes.keys())
    ancestors = {
        cls: _class_ancestor_locals(classes, cls) for cls in class_locals
    }
    accepted: set[str] = set()
    profile = context.contract.get("ordered_member_profile") or {}
    for item in profile.get("ordered_member_classes") or []:
        local = str(item).strip()
        if local in classes:
            accepted.add(local)

    def _accept_for_range(range_name: str, predicate_local: str = "") -> None:
        stem = _normalized_symbol(_predicate_target_stem(predicate_local))
        for cls in class_locals:
            if (
                cls == range_name
                or range_name in (ancestors.get(cls) or [])
                or (stem and _normalized_symbol(cls) == stem)
            ):
                accepted.add(cls)

    top_local = _top_entity_local(context)
    top_meta = classes.get(top_local) or {}
    for prop, range_local in (top_meta.get("object_properties") or {}).items():
        range_name = str(range_local).strip()
        if range_name:
            _accept_for_range(range_name, str(prop))
    for link in context.contract.get("required_links") or []:
        target = _iri_local((link or {}).get("target_class_iri"))
        pred = _iri_local((link or {}).get("predicate_iri"))
        if target:
            _accept_for_range(target, pred)
    return accepted


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _load_reasoner_module():
    path = ROOT / "scripts" / "validate_abox_with_reasoner.py"
    spec = importlib.util.spec_from_file_location("validate_abox_with_reasoner", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load reasoner module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unwrap_tool(fn: Any) -> Callable[..., Any] | None:
    if callable(fn):
        return fn
    inner = getattr(fn, "fn", None)
    if callable(inner):
        return inner
    return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_fixture(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Fixture must be a JSON object: {path}")
    return data


def _resolve_tbox_paths(raw: list[str] | None) -> list[Path]:
    if raw:
        return [Path(item) for item in raw]
    return list(DEFAULT_TBOX_PATHS)


def _tbox_fixture_inventory(context: AgenticGenerationContext) -> dict[str, Any]:
    """Compact T-Box inventory for full-coverage mock fixture generation."""
    classes_raw = context.parsed.get("classes") or {}
    properties_raw = context.parsed.get("properties") or {}
    parent_locals: set[str] = set()
    for meta in classes_raw.values():
        if not isinstance(meta, dict):
            continue
        for parent in meta.get("parent_classes") or []:
            local = str(parent).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
            if local:
                parent_locals.add(local)

    classes: dict[str, Any] = {}
    for name, meta in sorted(classes_raw.items()):
        if not isinstance(meta, dict):
            continue
        classes[name] = {
            "datatype_properties": meta.get("datatype_properties") or {},
            "object_properties": meta.get("object_properties") or {},
            "parent_classes": [
                str(p).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
                for p in (meta.get("parent_classes") or [])
            ],
            "is_also_parent": name in parent_locals,
        }

    properties: dict[str, Any] = {}
    for name, meta in sorted(properties_raw.items()):
        if not isinstance(meta, dict):
            properties[name] = {"kind": "unknown"}
            continue
        properties[name] = {
            "kind": meta.get("kind"),
            "domains": meta.get("domains") or [],
            "range": meta.get("range"),
        }

    ordered_member_classes = list(
        (context.contract.get("ordered_member_profile") or {}).get(
            "ordered_member_classes"
        )
        or []
    )
    top_level_hint_classes = _top_level_materializable_classes(context)
    nested_only_classes = sorted(set(classes_raw.keys()) - top_level_hint_classes)
    ordering_props = _ordering_property_locals(context)
    top_local = _top_entity_local(context)

    return {
        "all_class_locals": sorted(classes_raw.keys()),
        "all_property_locals": sorted(properties_raw.keys()),
        "top_level_hint_classes": sorted(top_level_hint_classes),
        "nested_only_classes": nested_only_classes,
        "classes": classes,
        "properties": properties,
        "required_links": context.contract.get("required_links") or [],
        "ordered_member_classes": ordered_member_classes,
        "ordering_property_locals": ordering_props,
        "primary_ordering_property": ordering_props[0] if ordering_props else None,
        "top_entity_local": top_local,
        "top_entity": context.contract.get("top_entity"),
    }


def _hint_property_locals_used(hints: dict[str, Any]) -> set[str]:
    used: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "label":
                    walk(value)
                    continue
                # Object-property hint keys often end with _label.
                prop = key[:-6] if key.endswith("_label") else key
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", str(prop)):
                    used.add(str(prop))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(hints)
    return used


def _fixture_coverage_gaps(
    context: AgenticGenerationContext, data: dict[str, Any]
) -> dict[str, Any]:
    inventory = _tbox_fixture_inventory(context)
    required_classes = set(inventory["all_class_locals"])
    required_props = set(inventory["all_property_locals"])
    top_level_required = set(inventory["top_level_hint_classes"])
    nested_only = set(inventory["nested_only_classes"])
    hints = data.get("hints") if isinstance(data.get("hints"), dict) else {}
    hint_classes = set(hints.keys())
    top_local = str(inventory.get("top_entity_local") or "").strip()
    # Top entity is materialized even if omitted from hints.
    present_top = set(hint_classes)
    if top_local:
        present_top.add(top_local)
    coverage_list = {
        str(x) for x in (data.get("coverage") or []) if str(x).strip()
    }
    used_props = _hint_property_locals_used(hints)
    declared_props = {
        str(x)
        for x in (data.get("property_coverage") or [])
        if str(x).strip()
    }
    # Nested-only classes must appear as *_label targets somewhere in hints.
    label_targets: set[str] = set()

    def walk_labels(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("_label") or key == "label":
                    if isinstance(value, list):
                        label_targets.update(str(v) for v in value if v is not None)
                    elif value is not None:
                        label_targets.add(str(value))
                walk_labels(value)
        elif isinstance(node, list):
            for item in node:
                walk_labels(item)

    walk_labels(hints)
    return {
        "missing_top_level_hint_classes": sorted(top_level_required - present_top),
        "forbidden_top_level_hint_classes": sorted(hint_classes & nested_only),
        "missing_coverage_list_classes": sorted(required_classes - coverage_list),
        "missing_properties_in_hints": sorted(required_props - used_props),
        "missing_properties_in_property_coverage": sorted(
            required_props - declared_props
        ),
        "extra_hint_classes": sorted(hint_classes - required_classes),
        "nested_only_classes": sorted(nested_only),
        "label_target_count": len(label_targets),
        "required_class_count": len(required_classes),
        "required_property_count": len(required_props),
        "top_level_hint_count": len(present_top & top_level_required),
        "used_property_count": len(used_props & required_props),
    }


def _fixture_prompt(
    context: AgenticGenerationContext,
    *,
    retry_gaps: dict[str, Any] | None = None,
) -> str:
    inventory = _tbox_fixture_inventory(context)
    classes = inventory["all_class_locals"]
    props = inventory["all_property_locals"]
    top_level = inventory["top_level_hint_classes"]
    nested_only = inventory["nested_only_classes"]
    ordering_prop = inventory.get("primary_ordering_property")
    top_local = inventory.get("top_entity_local") or "TopEntity"
    ordering_rule = (
        f"- Ordered-member classes need unique positive integer `{ordering_prop}` "
        "values across ALL ordered members."
        if ordering_prop
        else "- If the contract defines ordering properties, keep ordered-member values unique."
    )
    retry_block = ""
    if retry_gaps:
        retry_block = (
            "\n\nPrevious attempt missed full T-Box coverage or created unreachable nodes. Gaps:\n"
            + json.dumps(retry_gaps, ensure_ascii=False, indent=2)
            + "\nFix ALL gaps. Do not create top-level hints for nested-only classes.\n"
        )
    ordered_hint_shape = (
        f'[{{"label": "...", "{ordering_prop}": 1, "...": "..."}}]'
        if ordering_prop
        else '[{{"label": "...", "...": "..."}}]'
    )
    return textwrap.dedent(
        f"""
        You generate a mock fixture for semantic MCP testing of ontology `{context.ontology.name}`.
        The fixture MUST be derived only from the provided T-Box inventory (no outside vocabulary).

        Return only a valid JSON object with this exact shape:
        {{
          "document_md": "<English procedure markdown that mentions every class/property usage>",
          "hints": {{
            "<TopLevelClassLocal>": {{"label": "...", "<datatypeOrObjectHint>": "..."}},
            "<OrderedMemberClass>": {ordered_hint_shape}
          }},
          "coverage": ["<every class local from the T-Box>"],
          "property_coverage": ["<every property local from the T-Box>"]
        }}

        Hard requirements:
        - `coverage` MUST contain every class local exactly from: {json.dumps(classes)}.
        - `property_coverage` MUST contain every property local exactly from: {json.dumps(props)}.
        - Top-level `hints` keys MUST be ONLY from: {json.dumps(top_level)}.
          (These are classes materialize_hints can link under top entity `{top_local}`.)
        - Nested-only classes MUST NOT be top-level hint keys: {json.dumps(nested_only)}.
          Create them only via object-property `<predicate>_label` fields on already
          top-linked entities (labels must match created individuals).
        - For every property local in the T-Box, use it at least once in `hints`
          (datatype as scalar field; object property as `<predicate>_label`).
        - Every scalar / label in `hints` must be grounded in `document_md`.
        {ordering_rule}
        - Ordered member classes: {json.dumps(inventory["ordered_member_classes"])}.
        - Ordering properties from contract: {json.dumps(inventory["ordering_property_locals"])}.
        - Required top links: {json.dumps(inventory["required_links"], ensure_ascii=False)}.
        - Top entity: {json.dumps(inventory["top_entity"], ensure_ascii=False)}.
        - Do not invent class or property locals outside the T-Box inventory.
        - Keep values domain-plausible for one fictional but coherent procedure grounded in the T-Box.
        - Return only JSON. No markdown fences.

        T-Box inventory (authoritative):
        {json.dumps(inventory, ensure_ascii=False)}
        {retry_block}
        """
    ).strip()


def generate_mock_fixture(
    *,
    context: AgenticGenerationContext,
    model: str,
    dest: Path,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """LLM-generate a full T-Box coverage mock fixture (document + materialize hints)."""
    gaps: dict[str, Any] | None = None
    data: dict[str, Any] = {}
    for attempt in range(1, max(1, max_attempts) + 1):
        _log(f"[fixture] LLM full T-Box mock attempt {attempt}/{max_attempts}")
        result = invoke_json(model, _fixture_prompt(context, retry_gaps=gaps))
        data = result.data
        if not isinstance(data.get("hints"), dict):
            raise ValueError("Fixture LLM response missing object `hints`")
        if not isinstance(data.get("document_md"), str) or not data["document_md"].strip():
            raise ValueError("Fixture LLM response missing `document_md`")
        inventory = _tbox_fixture_inventory(context)
        data["coverage"] = list(
            data.get("coverage") or inventory["all_class_locals"]
        )
        data["property_coverage"] = list(
            data.get("property_coverage") or inventory["all_property_locals"]
        )
        gaps = _fixture_coverage_gaps(context, data)
        data["tbox_coverage_audit"] = gaps
        complete = (
            not gaps["missing_top_level_hint_classes"]
            and not gaps["forbidden_top_level_hint_classes"]
            and not gaps["missing_coverage_list_classes"]
            and not gaps["missing_properties_in_hints"]
            and not gaps["extra_hint_classes"]
        )
        data["tbox_coverage_complete"] = complete
        if complete:
            break
        _log(
            "[fixture] incomplete T-Box coverage: "
            f"missing_top={gaps['missing_top_level_hint_classes'][:8]} "
            f"forbidden_top={gaps['forbidden_top_level_hint_classes'][:8]} "
            f"missing_props={gaps['missing_properties_in_hints'][:8]}"
        )
    _write_json(dest, data)
    return data


def _coverage_present_in_graph(ttl_text: str, coverage: list[str]) -> dict[str, bool]:
    graph = Graph()
    graph.parse(data=ttl_text, format="turtle")
    found_locals: set[str] = set()
    for _, _, obj in graph.triples((None, RDF.type, None)):
        text = str(obj)
        local = text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        if local:
            found_locals.add(local)
    return {name: name in found_locals for name in coverage}


def run_mcp_harness(
    *,
    scripts_dir: Path,
    fixture: dict[str, Any],
    abox_path: Path,
    doi: str = "semantic-loop-mock-doi",
    top_name: str = "top",
    entity_label: str = "MockTopEntity",
) -> dict[str, Any]:
    """In-process materialize_hints (or tool_calls fallback) → write abox.ttl."""
    previous_data_dir = os.environ.get("TWA_AGENTIC_DATA_DIR")
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="ontosyn_mcp_harness_") as tmp_dir:
            os.environ["TWA_AGENTIC_DATA_DIR"] = tmp_dir
            module = _import_generated_main_module(scripts_dir, ONTOLOGY_NAME)
            materialize = _unwrap_tool(getattr(module, "materialize_hints", None))
            tool_calls = fixture.get("tool_calls")
            ttl = ""
            status = "error"
            message = ""
            created: Any = None

            if materialize is not None and isinstance(fixture.get("hints"), dict):
                raw = materialize(
                    doi,
                    top_name,
                    entity_label,
                    json.dumps(fixture["hints"], ensure_ascii=False),
                )
                try:
                    result = json.loads(str(raw or "{}"))
                except json.JSONDecodeError as exc:
                    return {
                        "ok": False,
                        "mode": "materialize_hints",
                        "error": f"non-JSON harness result: {exc}",
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                status = str(result.get("status") or "")
                message = str(result.get("message") or "")
                ttl = str(result.get("ttl") or "")
                created = result.get("created")
                if status != "ok" or not ttl.strip():
                    return {
                        "ok": False,
                        "mode": "materialize_hints",
                        "status": status,
                        "message": message,
                        "created": created,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
            elif isinstance(tool_calls, list) and tool_calls:
                call_log: list[dict[str, Any]] = []
                for call in tool_calls:
                    name = str((call or {}).get("tool") or "").strip()
                    args = (call or {}).get("args") or {}
                    if not isinstance(args, dict):
                        args = {}
                    fn = _unwrap_tool(getattr(module, name, None))
                    if fn is None:
                        return {
                            "ok": False,
                            "mode": "tool_calls",
                            "error": f"missing tool {name}",
                            "call_log": call_log,
                            "elapsed_seconds": round(time.perf_counter() - started, 3),
                        }
                    raw = fn(**args)
                    call_log.append({"tool": name, "raw": str(raw)[:500]})
                    if name in {"export_memory", "materialize_hints"}:
                        try:
                            parsed = json.loads(str(raw or "{}"))
                            if isinstance(parsed, dict) and parsed.get("ttl"):
                                ttl = str(parsed["ttl"])
                        except json.JSONDecodeError:
                            if isinstance(raw, str) and len(raw) > 20:
                                ttl = str(raw)
                if not ttl.strip():
                    export_fn = _unwrap_tool(getattr(module, "export_memory", None))
                    if export_fn is not None:
                        ttl = str(export_fn())
                if not ttl.strip():
                    return {
                        "ok": False,
                        "mode": "tool_calls",
                        "error": "tool_calls produced no TTL",
                        "call_log": call_log,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                status = "ok"
            else:
                return {
                    "ok": False,
                    "mode": "none",
                    "error": "fixture has neither usable hints nor tool_calls, "
                    "or materialize_hints is missing",
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }

            abox_path.parent.mkdir(parents=True, exist_ok=True)
            abox_path.write_text(ttl, encoding="utf-8")
            coverage = list(fixture.get("coverage") or [])
            present = _coverage_present_in_graph(ttl, coverage) if coverage else {}
            return {
                "ok": True,
                "mode": "materialize_hints" if materialize is not None else "tool_calls",
                "status": status,
                "message": message,
                "created": created,
                "abox_path": str(abox_path),
                "coverage_present": present,
                "coverage_ok": all(present.values()),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        if previous_data_dir is None:
            os.environ.pop("TWA_AGENTIC_DATA_DIR", None)
        else:
            os.environ["TWA_AGENTIC_DATA_DIR"] = previous_data_dir


def run_reasoner_gate(
    *,
    tbox_paths: list[Path],
    abox_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    """HermiT hard + unknown properties / domain-range hard.

    Unknown `rdf:type` alone is soft: cross-ontology ancestor typing noise in
    generated scripts must not block repair proofs focused on unknown properties.
    """
    reasoner = _load_reasoner_module()
    report = reasoner.validate(list(tbox_paths), [abox_path], run_hermit=True)
    hermit = report.get("hermit") or {}
    hermit_available = bool(hermit.get("available")) and "error" not in hermit
    hermit_consistent = hermit.get("consistent")
    details = report.get("details") or {}
    unknown_properties = list(details.get("unknown_properties") or [])
    domain_violations = list(details.get("domain_violations") or [])
    range_violations = list(details.get("range_violations") or [])
    om2_quantity_violations = list(details.get("om2_quantity_violations") or [])
    unknown_types = list(details.get("unknown_types") or [])
    warnings: list[str] = []
    failures_extra: list[str] = []

    if unknown_types:
        warnings.append(
            "soft: unknown rdf:type assertions ignored for this gate "
            f"({len(unknown_types)}): " + "; ".join(unknown_types[:5])
        )

    if not hermit_available:
        msg = "HermiT required but unavailable"
        if hermit.get("reason"):
            msg += f": {hermit['reason']}"
        if hermit.get("error"):
            msg += f": {hermit['error']}"
        failures_extra.append(msg)
        hermit_hard_fail = True
        ok = False
    elif hermit_consistent is False:
        failures_extra.append("HermiT reported ontology inconsistency")
        hermit_hard_fail = True
        ok = False
    else:
        hermit_hard_fail = False
        hard_owlrl = (
            unknown_properties
            + domain_violations
            + range_violations
            + om2_quantity_violations
        )
        ok = hermit_consistent is True and not hard_owlrl and not failures_extra
        if hard_owlrl:
            failures_extra.extend(hard_owlrl)

    merged_failures = list(dict.fromkeys(failures_extra))
    out = {
        **report,
        "ok": ok,
        "failures": merged_failures,
        "gate_warnings": warnings,
        "hermit_hard_fail": hermit_hard_fail,
        "hermit_required": True,
        "owlrl_ok": bool(report.get("ok")),
        "gate_mode": "hermit_plus_contract_structure",
    }
    _write_json(report_path, out)
    return out


def package_semantic_feedback(
    *,
    abox_build: dict[str, Any],
    reasoner: dict[str, Any] | None,
    coverage: list[str],
    ordering_property: str | None = None,
    top_entity_local: str | None = None,
) -> str:
    lines = [
        "# Semantic MCP feedback (feed into next MCP regeneration)",
        "",
        "## A-Box build",
        f"- ok: {abox_build.get('ok')}",
        f"- mode: {abox_build.get('mode')}",
        f"- error/message: {abox_build.get('error') or abox_build.get('message') or ''}",
    ]
    present = abox_build.get("coverage_present") or {}
    missing = [name for name in coverage if not present.get(name)]
    if missing:
        lines.append(f"- missing coverage classes in A-Box: {', '.join(missing)}")
    if reasoner is not None:
        lines.extend(
            [
                "",
                "## Reasoner (OWL-RL properties + HermiT required)",
                f"- ok: {reasoner.get('ok')}",
                f"- owlrl_ok: {reasoner.get('owlrl_ok')}",
                f"- hermit_hard_fail: {reasoner.get('hermit_hard_fail')}",
                f"- gate_mode: {reasoner.get('gate_mode')}",
            ]
        )
        failures = list(reasoner.get("failures") or [])[:40]
        if failures:
            lines.append("- failures:")
            lines.extend(f"  - {item}" for item in failures)
        for warning in reasoner.get("gate_warnings") or []:
            lines.append(f"- warning: {warning}")
    lines.extend(
        [
            "",
            "## Repair guidance",
            "- Emit only T-Box / linked-subgraph properties (never invented locals).",
            "- Preserve required top-entity links from the generation contract.",
            f"- If `{SEMANTIC_POISON_PROP}` appears, restore the real T-Box property local.",
            "- Prefer create_*/add_* locals that match the T-Box IRI local names exactly.",
            "- For every object property with an OM-2 range, create the declared "
            "OM-2 quantity type and exactly one hasNumericalValue and hasUnit.",
        ]
    )
    if ordering_property:
        lines.append(
            f"- Preserve unique positive `{ordering_property}` on ordered-member classes."
        )
    if top_entity_local:
        lines.append(f"- Keep the top entity typed as `{top_entity_local}`.")
    return "\n".join(lines) + "\n"


def _fact_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get("class") or ""),
        str(item.get("entity") or ""),
        str(item.get("property") or ""),
        str(item.get("value") or ""),
    )


def _content_gate_decision(
    *,
    content_report: dict[str, Any],
    fixture: dict[str, Any],
    champion_report: dict[str, Any] | None,
    semantic_ok: bool,
    hint_threshold: float,
    graph_threshold: float,
) -> dict[str, Any]:
    """Apply absolute, critical-slot, and champion-regression gates."""
    hints = content_report.get("hints") or {}
    graph = content_report.get("graph") or {}
    hint_metric = hints.get("overall") or {}
    graph_metric = graph.get("overall") or {}
    missing = list(hints.get("missing") or [])
    unexpected = list(hints.get("unexpected") or [])
    critical_slots = list(
        ((fixture.get("content_gt") or {}).get("critical_slots"))
        or fixture.get("critical_slots")
        or []
    )
    critical_failures = []
    for slot in critical_slots:
        class_local = str(slot.get("class") or "")
        property_local = str(slot.get("property") or "")
        slot_missing = [
            item
            for item in missing
            if item.get("class") == class_local
            and item.get("property") == property_local
        ]
        if slot_missing:
            critical_failures.append(
                {
                    "class": class_local,
                    "property": property_local,
                    "missing": slot_missing,
                }
            )

    absent_classes = set(
        ((fixture.get("content_gt") or {}).get("hints") or {}).get(
            "__absent_classes__", []
        )
        or (fixture.get("hints") or {}).get("__absent_classes__", [])
    )
    forbidden = [
        item for item in unexpected if str(item.get("class")) in absent_classes
    ]
    regressions: list[dict[str, Any]] = []
    if champion_report:
        champion_missing = {
            _fact_key(item)
            for item in ((champion_report.get("hints") or {}).get("missing") or [])
        }
        regressions = [
            item for item in missing if _fact_key(item) not in champion_missing
        ]

    failures = []
    hint_f1 = float(hint_metric.get("f1") or 0.0)
    graph_f1 = float(graph_metric.get("f1") or 0.0)
    if not semantic_ok:
        failures.append("semantic_or_reasoner")
    if hint_f1 < hint_threshold:
        failures.append("hint_f1_threshold")
    if graph_f1 < graph_threshold:
        failures.append("graph_f1_threshold")
    if critical_failures:
        failures.append("critical_slots")
    if forbidden:
        failures.append("forbidden_facts")
    if regressions:
        failures.append("champion_preserve_set")
    if champion_report:
        champion_hint = float(
            (((champion_report.get("hints") or {}).get("overall") or {}).get("f1"))
            or 0.0
        )
        champion_graph = float(
            (((champion_report.get("graph") or {}).get("overall") or {}).get("f1"))
            or 0.0
        )
        if hint_f1 < champion_hint:
            failures.append("hint_regression")
        if graph_f1 < champion_graph:
            failures.append("graph_regression")

    return {
        "accepted": not failures,
        "failures": failures,
        "critical_failures": critical_failures,
        "forbidden_facts": forbidden,
        "regressions": regressions,
        "metrics": {
            "hint_f1": hint_f1,
            "hint_recall": float(hint_metric.get("recall") or 0.0),
            "graph_f1": graph_f1,
            "graph_recall": float(graph_metric.get("recall") or 0.0),
        },
    }


def _candidate_rank(report: dict[str, Any]) -> tuple[float, float, float, float, int]:
    hints = (report.get("hints") or {}).get("overall") or {}
    graph = (report.get("graph") or {}).get("overall") or {}
    return (
        float(hints.get("recall") or 0.0),
        float(hints.get("f1") or 0.0),
        float(graph.get("recall") or 0.0),
        float(graph.get("f1") or 0.0),
        -int(hints.get("fp") or 0),
    )


def package_content_feedback(
    content_report: dict[str, Any],
    decision: dict[str, Any] | None = None,
    champion_report: dict[str, Any] | None = None,
) -> str:
    """Render content-score mismatches as prompt-agent feedback."""
    hint_score = content_report.get("hints") or {}
    graph_score = content_report.get("graph") or {}
    lines = [
        "# Prompt content feedback",
        "",
        "This feedback is for extraction/KG prompts, not Python script repair.",
        f"- hint F1: {(hint_score.get('overall') or {}).get('f1')}",
        f"- graph F1: {(graph_score.get('overall') or {}).get('f1')}",
    ]
    if decision:
        champion_hint = (
            ((champion_report or {}).get("hints") or {}).get("overall") or {}
        ).get("f1")
        candidate_hint = (hint_score.get("overall") or {}).get("f1")
        delta = (
            round(float(candidate_hint) - float(champion_hint), 4)
            if champion_hint is not None and candidate_hint is not None
            else None
        )
        lines.extend(
            [
                f"- champion hint F1: {champion_hint}",
                f"- candidate delta: {delta}",
                f"- decision: {'ACCEPTED' if decision.get('accepted') else 'REJECTED'}",
                f"- failed gates: {', '.join(decision.get('failures') or []) or 'none'}",
                "",
                "## Regressions introduced",
            ]
        )
        regressions = list(decision.get("regressions") or [])
        lines.extend(
            f"- {item.get('class')}[{item.get('entity')}].{item.get('property')} = "
            f"{item.get('value')}"
            for item in regressions
        )
        if not regressions:
            lines.append("- none")
    lines.extend([
        "",
        "## Missing source-grounded facts",
    ])
    missing = list(hint_score.get("missing") or [])[:80]
    lines.extend(
        f"- {item.get('class')}[{item.get('entity')}].{item.get('property')} = "
        f"{item.get('value')} (count={item.get('count')})"
        for item in missing
    )
    if not missing:
        lines.append("- none")
    lines.extend(["", "## Unexpected facts"])
    unexpected = list(hint_score.get("unexpected") or [])[:80]
    lines.extend(
        f"- {item.get('class')}[{item.get('entity')}].{item.get('property')} = "
        f"{item.get('value')} (count={item.get('count')})"
        for item in unexpected
    )
    if not unexpected:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Prompt adjustment rules",
            "- Preserve source-supported structured properties instead of collapsing them "
            "into generic free text.",
            "- Preserve exact object-label links between source operations and extracted targets.",
            "- Do not add fixture-specific labels, values, or benchmark facts to prompts.",
            "- Use only T-Box comments and the generation contract to generalise the correction.",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_ontosynthesis_mcp_launcher(artifact_root: Path) -> Path:
    create_init_files(artifact_root)
    launcher = artifact_root / "launch_ontosynthesis_mcp.py"
    launcher.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations

            import runpy
            import sys
            from pathlib import Path

            ROOT = Path(__file__).resolve().parent
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            runpy.run_module("scripts.ontosynthesis.main", run_name="__main__")
            """
        ),
        encoding="utf-8",
    )
    return launcher


def _write_ontosynthesis_react_mcp_config(
    *,
    artifact_root: Path,
    config_path: Path,
    data_dir: Path,
) -> str:
    launcher = _write_ontosynthesis_mcp_launcher(artifact_root)
    env = dict(os.environ)
    env["TWA_AGENTIC_DATA_DIR"] = str(data_dir.resolve())
    env["TWA_GENERATED_ARTIFACT_ROOT"] = str(artifact_root.resolve())
    payload = {
        "llm_created_mcp": {
            "command": sys.executable,
            "args": [str(launcher.resolve())],
            "transport": "stdio",
            "cwd": str(ROOT),
            "env": env,
        }
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return config_path.name


def _merge_ttl_files(ttl_paths: list[Path], dest: Path) -> dict[str, Any]:
    graph = Graph()
    loaded: list[str] = []
    for path in ttl_paths:
        if not path.is_file():
            continue
        graph.parse(str(path), format="turtle")
        loaded.append(str(path))
    if not loaded:
        return {"ok": False, "error": "no TTL files to merge", "loaded": []}
    dest.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(dest), format="turtle")
    return {
        "ok": True,
        "loaded": loaded,
        "triples": len(graph),
        "abox_path": str(dest),
    }


def run_react_pipeline_against_mock(
    *,
    artifact_root: Path,
    meta_task_config: Path,
    fixture: dict[str, Any],
    abox_path: Path,
    runtime_root: Path,
    doi: str = "semantic-mock-ontosynthesis-case",
) -> dict[str, Any]:
    """Run generated extraction and KG prompts against the fixture document."""
    from src.pipelines.main_kg_building.build import run_step as main_kg
    from src.pipelines.main_ontology_extractions.extract import run_step as main_extract
    from src.pipelines.top_entity_extraction.extract import run_step as top_extract
    from src.pipelines.top_entity_kg_building.build import run_step as top_kg

    started = time.perf_counter()
    document_md = str(fixture.get("document_md") or "").strip()
    if not document_md:
        return {"ok": False, "mode": "react", "error": "fixture missing document_md"}

    doi_hash = generate_hash(doi)
    data_dir = runtime_root.resolve()
    case_dir = data_dir / doi_hash
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    stitched = case_dir / f"{doi_hash}_stitched.md"
    stitched.write_text(document_md + "\n", encoding="utf-8")

    config_name = _write_ontosynthesis_react_mcp_config(
        artifact_root=artifact_root,
        config_path=ROOT / "configs/test_mcp_config_ontosynthesis_semantic_loop.json",
        data_dir=data_dir,
    )
    cfg = {
        "data_dir": str(data_dir),
        "project_root": str(ROOT),
        "meta_task_config": str(meta_task_config),
        "test_mcp_config": config_name,
        "force_react_kg": True,
        "skip_materialize_hints": True,
    }
    previous_artifact_root = os.environ.get("TWA_GENERATED_ARTIFACT_ROOT")
    previous_data_dir = os.environ.get("TWA_AGENTIC_DATA_DIR")
    previous_strict_root = os.environ.get("TWA_REQUIRE_GENERATED_ARTIFACT_ROOT")
    step_results: dict[str, bool] = {}
    try:
        os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = str(artifact_root.resolve())
        os.environ["TWA_AGENTIC_DATA_DIR"] = str(data_dir)
        os.environ["TWA_REQUIRE_GENERATED_ARTIFACT_ROOT"] = "1"
        for name, fn in (
            ("top_entity_extraction", top_extract),
            ("top_entity_kg_building", top_kg),
            ("main_ontology_extractions", main_extract),
            ("main_kg_building", main_kg),
        ):
            _log(f"[react] step {name} hash={doi_hash}")
            step_results[name] = bool(fn(doi_hash, cfg))
            if not step_results[name]:
                return {
                    "ok": False,
                    "mode": "react",
                    "doi_hash": doi_hash,
                    "case_dir": str(case_dir),
                    "step_results": step_results,
                    "error": f"pipeline step failed: {name}",
                }
        output = case_dir / "ontosynthesis_output"
        ttl_paths = sorted(output.glob("*.ttl")) if output.is_dir() else []
        if not ttl_paths:
            for pattern in ("iteration_1.ttl", "memory/*.ttl", "exports/*.ttl"):
                ttl_paths.extend(sorted(case_dir.glob(pattern)))
        merged = _merge_ttl_files(ttl_paths, abox_path)
        return {
            **merged,
            "mode": "react",
            "doi_hash": doi_hash,
            "case_dir": str(case_dir),
            "step_results": step_results,
            "predicted_hints": load_predicted_hints(case_dir),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "mode": "react",
            "doi_hash": doi_hash,
            "case_dir": str(case_dir),
            "step_results": step_results,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if previous_artifact_root is None:
            os.environ.pop("TWA_GENERATED_ARTIFACT_ROOT", None)
        else:
            os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = previous_artifact_root
        if previous_data_dir is None:
            os.environ.pop("TWA_AGENTIC_DATA_DIR", None)
        else:
            os.environ["TWA_AGENTIC_DATA_DIR"] = previous_data_dir
        if previous_strict_root is None:
            os.environ.pop("TWA_REQUIRE_GENERATED_ARTIFACT_ROOT", None)
        else:
            os.environ["TWA_REQUIRE_GENERATED_ARTIFACT_ROOT"] = previous_strict_root


def apply_semantic_feedback_repairs(
    *,
    context: AgenticGenerationContext,
    feedback_text: str,
    model: str,
    max_repairs: int,
    allow_llm: bool,
) -> list[dict[str, Any]]:
    """LLM-only in-place patches for semantic reasoner failures (no scripted undo)."""
    if not allow_llm or max_repairs <= 0 or not feedback_text.strip():
        return []
    scripts_dir = Path(context.scripts_dir)
    ontology = context.ontology.name
    entities_name = _entities_filename(ontology)
    entities = scripts_dir / entities_name
    ordering_prop = None
    try:
        ordering_prop = _primary_ordering_property(context)
    except ValueError:
        ordering_prop = None
    if entities.is_file() and SEMANTIC_POISON_PROP in entities.read_text(encoding="utf-8"):
        targets = [entities]
    else:
        targets = [
            p
            for p in (
                scripts_dir / entities_name,
                scripts_dir / f"{ontology}_creation_relationships.py",
                scripts_dir / f"{ontology}_creation_base.py",
                scripts_dir / "main.py",
            )
            if p.is_file()
        ]
    restore_hint = (
        f"restore the contract ordering property local `{ordering_prop}`"
        if ordering_prop
        else "restore the correct T-Box property local from the contract/inventory"
    )
    sticky = (
        feedback_text
        + "\n\n## LLM repair contract (non-trivial semantic defect)\n"
        + f"- If `{SEMANTIC_POISON_PROP}` appears as a property local in `_add_literal` / "
        + f"`_add_object`, {restore_hint}.\n"
        + "- Do not invent new property locals. Do not delete create_* tools.\n"
        + "- Prefer the smallest unified diff that restores T-Box-valid triples.\n"
    )

    def _goal_met(path: Path) -> bool:
        text = path.read_text(encoding="utf-8")
        return SEMANTIC_POISON_PROP not in text

    repairs: list[dict[str, Any]] = []
    for path in targets:
        _log(f"[semantic] LLM patch from reasoner feedback → {path.name}")
        repairs.append(
            repair_python_file_with_llm_for_goal(
                model=model,
                path=path,
                max_repairs=max_repairs,
                sticky_feedback=sticky,
                goal_met=_goal_met
                if path.name == entities_name
                else (lambda _p: True),
            )
        )
    return repairs


def exercise_level1_fail(scripts_dir: Path, *, mode: str = "syntax") -> list[str]:
    """Inject a non-trivial Level-1 defect that requires an LLM patch (not ruff --fix)."""
    changed: list[str] = []
    main_py = scripts_dir / "main.py"
    if not main_py.is_file():
        return changed
    text = main_py.read_text(encoding="utf-8")
    if LEVEL1_MARKER in text:
        return changed
    if mode == "ruff":
        # Kept only for unit tests of autofix plumbing; never used by --prove-repairs.
        injection = f"{LEVEL1_MARKER}\nimport level1_exercise_unused_module_xyz\n"
    else:
        # Broken syntax / indent that ruff --fix cannot heal — LLM must patch.
        injection = (
            f"{LEVEL1_MARKER}\n"
            "def __level1_exercise_broken_syntax((((:\n"
            "    return None\n"
        )
    main_py.write_text(injection + text, encoding="utf-8")
    changed.append(str(main_py))
    return changed


def _level1_healed_by_llm(level1: dict[str, Any]) -> bool:
    for item in level1.get("history") or []:
        if item.get("phase") == "ruff_llm_repair" and item.get("ok"):
            return True
        if item.get("phase") == "ruff_llm_repair" and item.get("patch_applied"):
            # repair_python_file_with_llm merges ok at top level of history entry
            if item.get("ok"):
                return True
    return False


def exercise_semantic_fail(
    scripts_dir: Path,
    *,
    ordering_property: str,
    ontology_name: str = ONTOLOGY_NAME,
) -> list[str]:
    """Rename the first ordering-property literal local so A-Box emits an unknown property."""
    changed: list[str] = []
    entities = scripts_dir / _entities_filename(ontology_name)
    if not entities.is_file():
        return changed
    text = entities.read_text(encoding="utf-8")
    prop = str(ordering_property).strip()
    if not prop:
        return changed
    # ruff format may rewrite quote style; match either.
    pattern = re.compile(
        rf'(_add_literal\(str\(iri\),\s*)([\'"]){re.escape(prop)}\2'
        rf"(\s*,\s*{re.escape(prop)}\))"
    )
    match = pattern.search(text)
    if match:
        quote = match.group(2)
        text = pattern.sub(
            rf"\1{quote}{SEMANTIC_POISON_PROP}{quote}\3",
            text,
            count=1,
        )
    else:
        fallback = re.compile(
            r'(_add_literal\(str\(iri\),\s*)([\'"])([A-Za-z0-9_]+)\2'
        )
        if not fallback.search(text):
            return changed
        text = fallback.sub(
            rf"\1\2{SEMANTIC_POISON_PROP}\2",
            text,
            count=1,
        )
    entities.write_text(text, encoding="utf-8")
    changed.append(str(entities))
    return changed


def regenerate_ontosynthesis_mcp(
    *,
    output_root: Path,
    meta_task_config: Path,
    feedback_path: Path | None,
    content_feedback_path: Path | None = None,
    llm_agent_generation: bool = True,
    generation_model: str = "gpt-5",
    max_agent_rounds: int = 2,
) -> AgenticGenerationContext:
    output_root.mkdir(parents=True, exist_ok=True)
    if feedback_path and feedback_path.is_file():
        sticky = output_root / "semantic_feedback.md"
        sticky.write_text(feedback_path.read_text(encoding="utf-8"), encoding="utf-8")
        _log(f"[regen] copied semantic feedback → {sticky}")
    if content_feedback_path and content_feedback_path.is_file():
        sticky_content = output_root / "content_feedback.md"
        sticky_content.write_text(
            content_feedback_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _log(f"[regen] copied prompt content feedback → {sticky_content}")

    _log(
        f"[regen] scripts/prompts generation "
        f"llm_agent={llm_agent_generation} model={generation_model}"
    )
    summary = run_agentic_generation_experiment(
        [ONTOLOGY_NAME],
        meta_task_config_path=meta_task_config,
        output_root=output_root,
        generate_scripts=True,
        generate_prompts=True,
        repair_loop=True,
        max_repair_iterations=2,
        llm_agent_generation=llm_agent_generation,
        generation_model=generation_model,
        max_agent_rounds=max_agent_rounds,
    )
    _write_json(output_root / "regen_summary.json", summary)
    context = build_agentic_generation_context(
        ontology_name=ONTOLOGY_NAME,
        meta_task_config_path=meta_task_config,
        output_root=output_root,
        write_files=True,
    )
    return context


def _context_from_scripts(
    *,
    scripts_dir: Path,
    meta_task_config: Path,
    output_root: Path,
) -> AgenticGenerationContext:
    """Build a generation context when scripts were copied rather than regenerated."""
    dest_scripts = output_root / "scripts" / ONTOLOGY_NAME
    if scripts_dir.resolve() != dest_scripts.resolve():
        if dest_scripts.exists():
            shutil.rmtree(dest_scripts)
        dest_scripts.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(scripts_dir, dest_scripts)
    artifact_source_root = scripts_dir.resolve().parents[1]
    for name in ("sparqls", "iterations", "ontology_structures"):
        source = artifact_source_root / name / ONTOLOGY_NAME
        destination = output_root / name / ONTOLOGY_NAME
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
    candidate_root = ROOT / "ai_generated_contents_candidate"
    sparql_destination = output_root / "sparqls" / ONTOLOGY_NAME
    if not list(sparql_destination.glob("*.sparql")):
        sparql_source = candidate_root / "sparqls" / ONTOLOGY_NAME
        if sparql_source.is_dir():
            shutil.copytree(
                sparql_source, sparql_destination, dirs_exist_ok=True
            )
    create_init_files(output_root)
    context = build_agentic_generation_context(
        ontology_name=ONTOLOGY_NAME,
        meta_task_config_path=meta_task_config,
        output_root=output_root,
        write_files=False,
    )
    generate_deterministic_prompt_slice(context)
    required_runtime_artifacts = [
        output_root
        / "sparqls"
        / ONTOLOGY_NAME
        / "top_entity_parsing.sparql",
        output_root / "iterations" / ONTOLOGY_NAME / "iterations.json",
        Path(context.contract_path),
    ]
    missing = [
        path.relative_to(output_root).as_posix()
        for path in required_runtime_artifacts
        if not path.is_file()
    ]
    if missing:
        raise RuntimeError(
            "Incomplete generated artifact package; missing: " + ", ".join(missing)
        )
    return context


def _prompt_only_regeneration(
    *,
    previous_root: Path,
    output_root: Path,
    meta_task_config: Path,
    content_feedback_path: Path,
    diagnosis_editor_path: Path,
    model: str,
    max_agent_rounds: int,
) -> AgenticGenerationContext:
    """Carry forward a validated package and let only the prompt agent adjust it."""
    for name in (
        "scripts",
        "prompts",
        "sparqls",
        "iterations",
        "ontology_structures",
        "reports",
    ):
        source = previous_root / name
        destination = output_root / name
        if source.exists():
            shutil.copytree(source, destination, dirs_exist_ok=True)
    create_init_files(output_root)
    (output_root / "content_feedback.md").write_text(
        content_feedback_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    context = build_agentic_generation_context(
        ontology_name=ONTOLOGY_NAME,
        meta_task_config_path=meta_task_config,
        output_root=output_root,
        write_files=True,
    )
    editor_diagnosis = json.loads(diagnosis_editor_path.read_text(encoding="utf-8"))

    def candidate_prompt_path(source: Any) -> str:
        name = Path(str(source)).name
        candidate = Path(context.prompts_dir) / name
        if not candidate.is_file():
            raise RuntimeError(
                f"Diagnosis target has no candidate prompt counterpart: {source}"
            )
        return candidate.resolve().as_posix()

    editor_diagnosis["target_prompt_set"] = [
        candidate_prompt_path(path)
        for path in editor_diagnosis.get("target_prompt_set") or []
    ]
    for issue in editor_diagnosis.get("issues") or []:
        if isinstance(issue, dict):
            issue["target_prompts"] = [
                candidate_prompt_path(path)
                for path in issue.get("target_prompts") or []
            ]
    _write_json(output_root / "content_diagnosis_editor.json", editor_diagnosis)
    targets = [
        Path(path) for path in editor_diagnosis.get("target_prompt_set") or []
    ]
    original_prompts = {
        path: path.read_text(encoding="utf-8") for path in targets
    }
    edit_attempts: list[dict[str, Any]] = []
    protocol: dict[str, Any] = {}
    final_report: dict[str, Any] = {}
    active_diagnosis = dict(editor_diagnosis)
    for validation_attempt in range(1, 4):
        for path, content in original_prompts.items():
            path.write_text(content, encoding="utf-8")
        protocol = run_structured_prompt_editor(
            model_name=model,
            output_root=output_root,
            targets=targets,
            diagnosis=active_diagnosis,
            contract=context.contract,
        )
        final_report = build_validation_report(
            context,
            foreign_contracts=None,
            write_report=True,
            prompts_required=True,
            extra_failures=list(protocol.get("failures") or []),
        )
        edit_attempts.append(
            {
                "validation_attempt": validation_attempt,
                "protocol": protocol,
                "validation": final_report,
            }
        )
        if protocol.get("ok") and final_report.get("ok"):
            break
        active_diagnosis = {
            **editor_diagnosis,
            "previous_static_validation_failures": final_report.get("failures")
            or [],
            "retry_instruction": (
                "Revise the proposed edit so all static prompt contracts pass. "
                "Do not repeat the rejected rule."
            ),
        }
    if not (protocol.get("ok") and final_report.get("ok")):
        for path, content in original_prompts.items():
            path.write_text(content, encoding="utf-8")
    summary = {
        "mode": "structured_prompt_editor",
        "model": model,
        "ok": bool(protocol.get("ok")) and bool(final_report.get("ok")),
        "prompt_protocol": protocol,
        "final_report": final_report,
        "edit_attempts": edit_attempts,
    }
    _write_json(output_root / "prompt_enhancement_summary.json", summary)
    if not summary.get("ok"):
        raise RuntimeError(
            "Prompt enhancement protocol failed: "
            + "; ".join(
                str(item)
                for item in (summary.get("final_report") or {}).get("failures") or []
            )
        )
    return context


def run_outer_loop(
    *,
    output_root: Path,
    meta_task_config: Path,
    tbox_paths: list[Path],
    max_outer: int,
    max_ruff_repairs: int,
    model: str,
    fixture_path: Path | None,
    allow_llm: bool,
    exercise_semantic: bool,
    exercise_level1: bool = False,
    scripts_source: Path | None = None,
    llm_agent_generation: bool = True,
    generation_model: str = "gpt-5",
    max_agent_rounds: int = 2,
    abox_mode: str = "harness",
    enhance_prompts: bool = False,
    content_f1_threshold: float = 0.95,
    graph_f1_threshold: float = 0.0,
    evaluation_repeats: int = 1,
) -> dict[str, Any]:
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + f"-{uuid.uuid4().hex[:8]}"
    )
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    iterations: list[dict[str, Any]] = []
    feedback_path: Path | None = None
    content_feedback_path: Path | None = None
    diagnosis_editor_path: Path | None = None
    overall_ok = False
    champion_dir: Path | None = None
    champion_report: dict[str, Any] | None = None
    champion_iteration: int | None = None
    accepted_champion_iteration: int | None = None
    best_observed_iteration: int | None = None
    best_observed_report: dict[str, Any] | None = None

    for outer in range(max(1, max_outer)):
        iter_dir = run_dir / f"iter_{outer}"
        if iter_dir.exists():
            shutil.rmtree(iter_dir)
        iter_dir.mkdir(parents=True, exist_ok=True)

        if (
            content_feedback_path is not None
            and diagnosis_editor_path is not None
            and outer > 0
        ):
            _log(f"[outer {outer}] prompt-only enhancement from content feedback")
            context = _prompt_only_regeneration(
                previous_root=champion_dir or run_dir / f"iter_{outer - 1}",
                output_root=iter_dir,
                meta_task_config=meta_task_config,
                content_feedback_path=content_feedback_path,
                diagnosis_editor_path=diagnosis_editor_path,
                model=generation_model,
                max_agent_rounds=max_agent_rounds,
            )
        elif scripts_source is not None and outer == 0:
            _log(f"[outer {outer}] copy existing OntoSyn scripts → {iter_dir}")
            context = _context_from_scripts(
                scripts_dir=scripts_source,
                meta_task_config=meta_task_config,
                output_root=iter_dir,
            )
        else:
            _log(f"[outer {outer}] regenerate OntoSyn MCP → {iter_dir}")
            context = regenerate_ontosynthesis_mcp(
                output_root=iter_dir,
                meta_task_config=meta_task_config,
                feedback_path=feedback_path,
                content_feedback_path=content_feedback_path,
                llm_agent_generation=llm_agent_generation,
                generation_model=generation_model,
                max_agent_rounds=max_agent_rounds,
            )
            create_init_files(iter_dir)

        scripts_dir = Path(context.scripts_dir)
        initial_manifest = artifact_manifest(iter_dir)
        _write_json(
            iter_dir / "artifact_manifest_initial.json",
            {
                "parent_iteration": champion_iteration,
                "manifest": initial_manifest,
                "manifest_sha256": json_digest(initial_manifest),
            },
        )
        semantic_repairs: list[dict[str, Any]] = []
        if feedback_path and feedback_path.is_file():
            semantic_repairs = apply_semantic_feedback_repairs(
                context=context,
                feedback_text=feedback_path.read_text(encoding="utf-8"),
                model=model,
                max_repairs=max(1, max_ruff_repairs),
                allow_llm=allow_llm,
            )

        level1_injected: list[str] = []
        if exercise_level1 and outer == 0:
            if not allow_llm:
                raise ValueError(
                    "--exercise-level1-fail injects a non-trivial syntax defect; "
                    "LLM repair is required (omit --no-llm)."
                )
            level1_injected = exercise_level1_fail(scripts_dir, mode="syntax")
            _log(f"[outer {outer}] exercise-level1-fail mutated: {level1_injected}")

        _log(f"[outer {outer}] Level-1 ruff/contract repair")
        if enhance_prompts:
            level1_static = run_ruff_on_scripts(scripts_dir)
            level1_validation = build_validation_report(
                context, foreign_contracts=None, write_report=True
            )
            level1 = {
                "ok": bool(level1_static.get("ok"))
                and bool(level1_validation.get("ok")),
                "ruff": level1_static,
                "validation": level1_validation,
                "history": [
                    {
                        "phase": "prompt_only_static_validation",
                        "scripts_mutable": False,
                    }
                ],
            }
        else:
            level1 = level1_repair_loop(
                context=context,
                model=model,
                max_ruff_repairs=max_ruff_repairs,
                allow_llm=allow_llm,
                log=_log,
            )
        if not level1.get("ok"):
            iter_report = {
                "outer": outer,
                "ok": False,
                "stage_failed": "level1",
                "level1": level1,
                "level1_injected": level1_injected,
                "semantic_repairs": semantic_repairs,
            }
            iterations.append(iter_report)
            _write_json(iter_dir / "iter_report.json", iter_report)
            feedback_path = iter_dir / "semantic_feedback.md"
            feedback_path.write_text(
                "# Level-1 failures blocked semantic stage\n\n"
                + "\n".join(
                    f"- {f}"
                    for f in (level1.get("validation") or {}).get("failures") or []
                )
                + "\n",
                encoding="utf-8",
            )
            continue

        poisoned: list[str] = []
        if exercise_semantic and outer == 0:
            if not allow_llm:
                raise ValueError(
                    "--exercise-semantic-fail injects a non-trivial T-Box defect; "
                    "LLM repair is required (omit --no-llm)."
                )
            poisoned = exercise_semantic_fail(
                scripts_dir,
                ordering_property=_primary_ordering_property(context),
                ontology_name=context.ontology.name,
            )
            _log(f"[outer {outer}] exercise-semantic-fail mutated: {poisoned}")

        fixture_dest = iter_dir / "fixture.json"
        if fixture_path is not None:
            fixture = _load_fixture(fixture_path)
            _write_json(fixture_dest, fixture)
            fixture_source = "file"
        elif allow_llm:
            _log(f"[outer {outer}] generating full T-Box mock fixture via LLM ({model})")
            fixture = generate_mock_fixture(
                context=context, model=model, dest=fixture_dest
            )
            fixture_source = "llm_full_tbox"
            _log(
                f"[outer {outer}] fixture coverage complete="
                f"{fixture.get('tbox_coverage_complete')} "
                f"audit={json.dumps(fixture.get('tbox_coverage_audit') or {}, ensure_ascii=False)[:500]}"
            )
        else:
            raise ValueError("No --fixture provided and LLM disabled (--no-llm)")

        oracle_abox_path = iter_dir / "oracle_abox.ttl"
        _log(f"[outer {outer}] MCP harness oracle materialize → {oracle_abox_path}")
        oracle_build = run_mcp_harness(
            scripts_dir=scripts_dir,
            fixture=fixture,
            abox_path=oracle_abox_path,
        )
        abox_path = oracle_abox_path
        abox_build = oracle_build
        content_report: dict[str, Any] | None = None
        repeat_reports: list[dict[str, Any]] = []
        reasoner_report: dict[str, Any] | None = None
        if abox_mode == "react":
            gold_hints = (
                ((fixture.get("content_gt") or {}).get("hints"))
                or fixture.get("hints")
                or {}
            )
            for repeat_index in range(max(1, evaluation_repeats)):
                repeat_dir = iter_dir / "evaluations" / f"run_{repeat_index + 1}"
                repeat_dir.mkdir(parents=True, exist_ok=True)
                repeat_abox = repeat_dir / "react_abox.ttl"
                repeat_build = run_react_pipeline_against_mock(
                    artifact_root=iter_dir,
                    meta_task_config=meta_task_config,
                    fixture=fixture,
                    abox_path=repeat_abox,
                    runtime_root=repeat_dir / "react_runtime",
                )
                repeat_content = {
                    "hints": score_hint_content(
                        gold_hints, repeat_build.get("predicted_hints") or {}
                    ),
                    "graph": score_graph_content(oracle_abox_path, repeat_abox)
                    if oracle_build.get("ok") and repeat_build.get("ok")
                    else {
                        "ok": False,
                        "overall": {"f1": 0.0, "recall": 0.0},
                        "error": "oracle or react A-Box build failed",
                    },
                }
                repeat_reasoner = (
                    run_reasoner_gate(
                        tbox_paths=tbox_paths,
                        abox_path=repeat_abox,
                        report_path=repeat_dir / "reasoner_report.json",
                    )
                    if repeat_build.get("ok")
                    else None
                )
                repeat_reports.append(
                    {
                        "index": repeat_index + 1,
                        "abox_path": str(repeat_abox),
                        "abox_build": repeat_build,
                        "content_score": repeat_content,
                        "reasoner": repeat_reasoner,
                    }
                )
                _write_json(repeat_dir / "content_score.json", repeat_content)

            worst = min(
                repeat_reports,
                key=lambda item: (
                    float(
                        (
                            (
                                item["content_score"].get("hints") or {}
                            ).get("overall")
                            or {}
                        ).get("f1")
                        or 0.0
                    ),
                    float(
                        (
                            (
                                item["content_score"].get("graph") or {}
                            ).get("overall")
                            or {}
                        ).get("f1")
                        or 0.0
                    ),
                ),
            )
            abox_path = Path(worst["abox_path"])
            abox_build = worst["abox_build"]
            content_report = worst["content_score"]
            content_report["repeats"] = [
                {
                    "index": item["index"],
                    "semantic_ok": bool(item["abox_build"].get("ok"))
                    and bool(item["reasoner"] and item["reasoner"].get("ok")),
                    "hint_f1": (
                        ((item["content_score"].get("hints") or {}).get("overall") or {})
                    ).get("f1"),
                    "graph_f1": (
                        ((item["content_score"].get("graph") or {}).get("overall") or {})
                    ).get("f1"),
                }
                for item in repeat_reports
            ]
            reasoner_report = worst["reasoner"]
            _write_json(iter_dir / "content_score.json", content_report)
        if isinstance(fixture.get("tbox_coverage_audit"), dict):
            abox_build = {
                **abox_build,
                "tbox_coverage_complete": fixture.get("tbox_coverage_complete"),
                "tbox_coverage_audit": fixture.get("tbox_coverage_audit"),
            }

        if abox_build.get("ok") and reasoner_report is None:
            _log(f"[outer {outer}] HermiT reasoner gate")
            reasoner_report = run_reasoner_gate(
                tbox_paths=tbox_paths,
                abox_path=abox_path,
                report_path=iter_dir / "reasoner_report.json",
            )
        _write_json(iter_dir / "abox_build_report.json", abox_build)

        coverage = list(
            fixture.get("coverage")
            or _tbox_fixture_inventory(context)["all_class_locals"]
        )
        semantic_ok = bool(abox_build.get("ok")) and bool(
            reasoner_report and reasoner_report.get("ok")
        )
        decision: dict[str, Any] | None = None
        if content_report is not None:
            all_repeat_semantic = all(
                bool(item.get("semantic_ok"))
                for item in content_report.get("repeats") or [
                    {"semantic_ok": semantic_ok}
                ]
            )
            decision = _content_gate_decision(
                content_report=content_report,
                fixture=fixture,
                champion_report=champion_report,
                semantic_ok=semantic_ok and all_repeat_semantic,
                hint_threshold=content_f1_threshold,
                graph_threshold=graph_f1_threshold,
            )
            repeat_gate_failures = []
            for repeat in repeat_reports:
                repeat_semantic_ok = bool(repeat["abox_build"].get("ok")) and bool(
                    repeat["reasoner"] and repeat["reasoner"].get("ok")
                )
                repeat_decision = _content_gate_decision(
                    content_report=repeat["content_score"],
                    fixture=fixture,
                    champion_report=None,
                    semantic_ok=repeat_semantic_ok,
                    hint_threshold=content_f1_threshold,
                    graph_threshold=graph_f1_threshold,
                )
                if not repeat_decision["accepted"]:
                    repeat_gate_failures.append(
                        {
                            "index": repeat["index"],
                            "failures": repeat_decision["failures"],
                            "critical_failures": repeat_decision["critical_failures"],
                        }
                    )
            if repeat_gate_failures:
                decision["accepted"] = False
                if "repeat_stability" not in decision["failures"]:
                    decision["failures"].append("repeat_stability")
                decision["repeat_gate_failures"] = repeat_gate_failures
            content_report["decision"] = decision
            content_report["ok"] = bool(decision["accepted"])
            _write_json(iter_dir / "content_score.json", content_report)
            content_feedback_path = iter_dir / "content_feedback.md"
            content_feedback_path.write_text(
                package_content_feedback(
                    content_report,
                    decision=decision,
                    champion_report=champion_report,
                ),
                encoding="utf-8",
            )
            if enhance_prompts and not decision.get("accepted"):
                inventory = prompt_inventory(Path(context.prompts_dir))
                if not inventory:
                    raise RuntimeError(
                        "Prompt diagnosis requires a non-empty prompt inventory"
                    )
                diagnosis_input = {
                    "schema_version": "content-diagnosis-input.v1",
                    "mock_source": fixture.get("document_md"),
                    "gold_hints": gold_hints,
                    "predicted_hints": abox_build.get("predicted_hints") or {},
                    "hint_differences": {
                        "missing": (content_report.get("hints") or {}).get("missing")
                        or [],
                        "unexpected": (content_report.get("hints") or {}).get(
                            "unexpected"
                        )
                        or [],
                    },
                    "graph_differences": {
                        "missing": (content_report.get("graph") or {}).get("missing")
                        or [],
                        "unexpected": (content_report.get("graph") or {}).get(
                            "unexpected"
                        )
                        or [],
                    },
                    "repeat_results": content_report.get("repeats") or [],
                    "decision": decision,
                    "prompt_inventory": inventory,
                    "contract": {
                        "top_entity": context.contract.get("top_entity"),
                        "ordered_member_profile": context.contract.get(
                            "ordered_member_profile"
                        ),
                        "required_links": context.contract.get("required_links"),
                        "required_step_scoped_object_properties": context.contract.get(
                            "required_step_scoped_object_properties"
                        ),
                    },
                }
                diagnosis_input["input_sha256"] = json_digest(diagnosis_input)
                _write_json(iter_dir / "diagnosis_input.json", diagnosis_input)
                diagnosis_manifest_before = artifact_manifest(iter_dir)
                diagnosis_run = run_content_diagnosis_agent_sync(
                    model_name=generation_model,
                    payload=diagnosis_input,
                    inventory=inventory,
                )
                if artifact_manifest(iter_dir) != diagnosis_manifest_before:
                    raise RuntimeError(
                        "Read-only diagnosis agent modified generated artifacts"
                    )
                diagnosis = diagnosis_run["diagnosis"]
                _write_json(
                    iter_dir / "diagnosis_output.json",
                    {
                        **diagnosis_run,
                        "output_sha256": json_digest(diagnosis),
                    },
                )
                if diagnosis.get("status") != "actionable":
                    raise RuntimeError(
                        f"GPT diagnosis is not actionable: {diagnosis.get('status')}"
                    )
                editor_diagnosis = redact_diagnosis(
                    diagnosis, fixture_literals(fixture)
                )
                diagnosis_editor_path = iter_dir / "content_diagnosis_editor.json"
                _write_json(diagnosis_editor_path, editor_diagnosis)
        content_ok = (
            True
            if content_report is None or not enhance_prompts
            else bool(content_report.get("ok"))
        )
        iteration_ok = semantic_ok and content_ok
        champion_updated = False
        if content_report is not None and (
            best_observed_report is None
            or _candidate_rank(content_report) > _candidate_rank(best_observed_report)
        ):
            best_observed_report = content_report
            best_observed_iteration = outer
        if semantic_ok and content_report is not None:
            is_better = (
                champion_report is None
                or _candidate_rank(content_report) > _candidate_rank(champion_report)
            )
            if is_better and (
                outer == 0 or bool(decision and decision.get("accepted"))
            ):
                champion_dir = iter_dir
                champion_report = content_report
                champion_iteration = outer
                if decision and decision.get("accepted"):
                    accepted_champion_iteration = outer
                champion_updated = True
                _write_json(
                    run_dir / "champion.json",
                    {
                        "iteration": outer,
                        "artifact_root": str(iter_dir),
                        "rank": list(_candidate_rank(content_report)),
                        "content_score": content_report,
                    },
                )
        ordering_prop = None
        try:
            ordering_prop = _primary_ordering_property(context)
        except ValueError:
            ordering_prop = None
        feedback_text = package_semantic_feedback(
            abox_build=abox_build,
            reasoner=reasoner_report,
            coverage=coverage,
            ordering_property=ordering_prop,
            top_entity_local=_top_entity_local(context),
        )
        feedback_path = iter_dir / "semantic_feedback.md"
        feedback_path.write_text(feedback_text, encoding="utf-8")

        iter_report = {
            "outer": outer,
            "ok": iteration_ok,
            "abox_mode": abox_mode,
            "fixture_source": fixture_source,
            "fixture_path": str(fixture_dest),
            "tbox_coverage_complete": fixture.get("tbox_coverage_complete"),
            "tbox_coverage_audit": fixture.get("tbox_coverage_audit"),
            "generation_model": generation_model,
            "llm_agent_generation": llm_agent_generation,
            "level1": {
                "ok": level1.get("ok"),
                "validation_failures": (level1.get("validation") or {}).get("failures"),
            },
            "level1_injected": level1_injected,
            "semantic_repairs": semantic_repairs,
            "exercise_semantic_fail": poisoned,
            "abox_build": abox_build,
            "oracle_abox_build": oracle_build,
            "content_score": content_report,
            "candidate_decision": decision,
            "champion_updated": champion_updated,
            "champion_iteration": champion_iteration,
            "artifact_manifest_initial": str(
                iter_dir / "artifact_manifest_initial.json"
            ),
            "artifact_manifest_final": str(
                iter_dir / "artifact_manifest_final.json"
            ),
            "content_feedback_path": str(content_feedback_path)
            if content_feedback_path is not None
            else None,
            "reasoner": {
                "ok": None if reasoner_report is None else reasoner_report.get("ok"),
                "owlrl_ok": None
                if reasoner_report is None
                else reasoner_report.get("owlrl_ok"),
                "failures": None
                if reasoner_report is None
                else reasoner_report.get("failures"),
                "hermit_hard_fail": None
                if reasoner_report is None
                else reasoner_report.get("hermit_hard_fail"),
                "report_path": str(iter_dir / "reasoner_report.json")
                if reasoner_report is not None
                else None,
            },
            "feedback_path": str(feedback_path),
        }
        final_manifest = artifact_manifest(iter_dir)
        _write_json(
            iter_dir / "artifact_manifest_final.json",
            {
                "manifest": final_manifest,
                "manifest_sha256": json_digest(final_manifest),
                "scripts_unchanged": {
                    path: digest
                    for path, digest in initial_manifest.items()
                    if path.startswith("scripts/")
                }
                == {
                    path: digest
                    for path, digest in final_manifest.items()
                    if path.startswith("scripts/")
                },
            },
        )
        if iteration_ok:
            iterations.append(iter_report)
            _write_json(iter_dir / "iter_report.json", iter_report)
            overall_ok = True
            _log(f"[outer {outer}] PASS")
            break

        # Non-trivial reasoner failures: LLM in-place patch on the same package.
        # Regenerating later is optional recovery, not a counted scripted "fix".
        in_place_healed = False
        if (
            allow_llm
            and reasoner_report is not None
            and not reasoner_report.get("ok")
            and abox_mode == "harness"
        ):
            _log(f"[outer {outer}] LLM in-place semantic repair (non-trivial)")
            in_place_repairs = apply_semantic_feedback_repairs(
                context=context,
                feedback_text=feedback_text,
                model=model,
                max_repairs=max(1, max_ruff_repairs),
                allow_llm=True,
            )
            semantic_repairs = list(semantic_repairs) + list(in_place_repairs)
            abox_build = run_mcp_harness(
                scripts_dir=scripts_dir,
                fixture=fixture,
                abox_path=abox_path,
            )
            reasoner_report = None
            if abox_build.get("ok"):
                reasoner_report = run_reasoner_gate(
                    tbox_paths=tbox_paths,
                    abox_path=abox_path,
                    report_path=iter_dir / "reasoner_report_after_llm.json",
                )
            in_place_healed = bool(abox_build.get("ok")) and bool(
                reasoner_report and reasoner_report.get("ok")
            )
            feedback_text = package_semantic_feedback(
                abox_build=abox_build,
                reasoner=reasoner_report,
                coverage=coverage,
                ordering_property=ordering_prop,
                top_entity_local=_top_entity_local(context),
            )
            feedback_path.write_text(feedback_text, encoding="utf-8")
            iter_report["semantic_repairs"] = semantic_repairs
            iter_report["llm_in_place_semantic"] = {
                "attempted": True,
                "healed": in_place_healed,
                "reasoner_ok": None
                if reasoner_report is None
                else reasoner_report.get("ok"),
                "failures": None
                if reasoner_report is None
                else reasoner_report.get("failures"),
            }
            iter_report["ok"] = in_place_healed
            iter_report["abox_build"] = abox_build
            if reasoner_report is not None:
                iter_report["reasoner"] = {
                    "ok": reasoner_report.get("ok"),
                    "owlrl_ok": reasoner_report.get("owlrl_ok"),
                    "failures": reasoner_report.get("failures"),
                    "hermit_hard_fail": reasoner_report.get("hermit_hard_fail"),
                    "report_path": str(iter_dir / "reasoner_report_after_llm.json"),
                }

        iterations.append(iter_report)
        _write_json(iter_dir / "iter_report.json", iter_report)

        if semantic_ok and not content_ok:
            # Content misses belong to the prompt-only channel. Do not trigger
            # coding-agent semantic repairs on a graph that already passed.
            feedback_path = None

        if in_place_healed:
            overall_ok = True
            _log(f"[outer {outer}] PASS after LLM in-place semantic repair")
            break
        _log(f"[outer {outer}] FAIL — feedback ready for next regenerate")

    summary = {
        "ok": overall_ok,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "ontology": ONTOLOGY_NAME,
        "abox_mode": abox_mode,
        "enhance_prompts": enhance_prompts,
        "content_f1_threshold": content_f1_threshold,
        "graph_f1_threshold": graph_f1_threshold,
        "evaluation_repeats": evaluation_repeats,
        "champion_iteration": champion_iteration,
        "champion_path": str(champion_dir) if champion_dir is not None else None,
        "baseline_iteration": 0 if iterations else None,
        "best_observed_iteration": best_observed_iteration,
        "accepted_champion_iteration": accepted_champion_iteration,
        "hermit_required": True,
        "llm_agent_generation": llm_agent_generation,
        "generation_model": generation_model,
        "fixture_model": model,
        "outer_iterations": len(iterations),
        "max_outer": max_outer,
        "meta_task_config": str(meta_task_config),
        "tbox_paths": [str(p) for p in tbox_paths],
        "iterations": iterations,
    }
    _write_json(run_dir / "summary.json", summary)
    _log(f"[done] ok={overall_ok} summary={run_dir / 'summary.json'}")
    return summary


def run_prove_repairs(
    *,
    output_root: Path,
    meta_task_config: Path,
    tbox_paths: list[Path],
    fixture_path: Path,
    model: str,
    max_ruff_repairs: int,
    allow_llm: bool,
    scripts_source: Path | None = None,
) -> dict[str, Any]:
    """Prove L1+L2 heals via LLM patches only (no restore/regenerate shortcuts)."""
    if not allow_llm:
        raise ValueError(
            "--prove-repairs requires LLM repairs for non-trivial defects "
            "(omit --no-llm)."
        )
    if max_ruff_repairs <= 0:
        raise ValueError(
            "--prove-repairs requires --max-ruff-repairs >= 1 so the LLM can patch."
        )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / f"prove_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    fixture = _load_fixture(fixture_path)
    _write_json(run_dir / "fixture.json", fixture)

    if scripts_source is not None:
        _log(f"[prove] copy existing scripts → {run_dir}")
        context = _context_from_scripts(
            scripts_dir=scripts_source,
            meta_task_config=meta_task_config,
            output_root=run_dir,
        )
        source_mode = "copy"
    else:
        _log(f"[prove] regenerate OntoSyn MCP → {run_dir}")
        context = regenerate_ontosynthesis_mcp(
            output_root=run_dir,
            meta_task_config=meta_task_config,
            feedback_path=None,
        )
        create_init_files(run_dir)
        source_mode = "regenerate"

    scripts_dir = Path(context.scripts_dir)
    proof: dict[str, Any] = {
        "source_mode": source_mode,
        "repair_policy": "llm_patch_only",
        "level1_proof": {},
        "baseline": {},
        "semantic_proof": {},
    }

    # --- Level-1 proof (syntax; ruff --fix must not be enough) ---
    injected_l1 = exercise_level1_fail(scripts_dir, mode="syntax")
    ruff_before = run_ruff_on_scripts(scripts_dir)
    failed_before = not bool(ruff_before.get("ok"))
    autofix_probe = None
    if failed_before:
        # Demonstrate non-triviality: autofix alone leaves the package broken.
        autofix_probe = autofix_ruff_on_scripts(scripts_dir)
        still_broken_after_autofix = not bool(
            (autofix_probe.get("recheck") or {}).get("ok")
        )
    else:
        still_broken_after_autofix = False
    _log(
        f"[prove] Level-1 injected={injected_l1} failed_before={failed_before} "
        f"still_broken_after_autofix={still_broken_after_autofix}"
    )
    level1 = level1_repair_loop(
        context=context,
        model=model,
        max_ruff_repairs=max_ruff_repairs,
        allow_llm=True,
        log=_log,
    )
    healed_by_llm = _level1_healed_by_llm(level1)
    level1_healed = bool(
        failed_before
        and still_broken_after_autofix
        and level1.get("ok")
        and healed_by_llm
    )
    proof["level1_proof"] = {
        "injected": bool(injected_l1),
        "mode": "syntax",
        "failed_before_repair": failed_before,
        "still_broken_after_ruff_autofix": still_broken_after_autofix,
        "healed": level1_healed,
        "healed_by": "llm_patch" if level1_healed else None,
        "level1_ok": bool(level1.get("ok")),
        "autofix_probe_ok": None
        if autofix_probe is None
        else bool((autofix_probe.get("recheck") or {}).get("ok")),
    }
    _write_json(run_dir / "level1_proof.json", proof["level1_proof"])
    if not level1_healed:
        summary = {
            "ok": False,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "mode": "prove_repairs",
            "proof": proof,
            "stage_failed": "level1",
        }
        _write_json(run_dir / "summary.json", summary)
        return summary

    # --- Baseline green path ---
    abox_path = run_dir / "abox_baseline.ttl"
    abox_build = run_mcp_harness(
        scripts_dir=scripts_dir, fixture=fixture, abox_path=abox_path
    )
    reasoner_baseline = None
    if abox_build.get("ok"):
        reasoner_baseline = run_reasoner_gate(
            tbox_paths=tbox_paths,
            abox_path=abox_path,
            report_path=run_dir / "reasoner_baseline.json",
        )
    baseline_ok = bool(abox_build.get("ok")) and bool(
        reasoner_baseline and reasoner_baseline.get("ok")
    )
    proof["baseline"] = {
        "abox_ok": bool(abox_build.get("ok")),
        "reasoner_ok": None
        if reasoner_baseline is None
        else bool(reasoner_baseline.get("ok")),
        "ok": baseline_ok,
        "failures": None if reasoner_baseline is None else reasoner_baseline.get("failures"),
    }
    _write_json(run_dir / "baseline.json", proof["baseline"])
    if not baseline_ok:
        summary = {
            "ok": False,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "mode": "prove_repairs",
            "proof": proof,
            "stage_failed": "baseline",
        }
        _write_json(run_dir / "summary.json", summary)
        return summary

    # --- Level-2 / semantic proof (LLM patch only; no restore/regenerate) ---
    ordering_prop = _primary_ordering_property(context)
    poisoned = exercise_semantic_fail(
        scripts_dir,
        ordering_property=ordering_prop,
        ontology_name=context.ontology.name,
    )
    abox_poison = run_dir / "abox_poisoned.ttl"
    abox_poison_build = run_mcp_harness(
        scripts_dir=scripts_dir, fixture=fixture, abox_path=abox_poison
    )
    reasoner_poison = None
    if abox_poison_build.get("ok"):
        reasoner_poison = run_reasoner_gate(
            tbox_paths=tbox_paths,
            abox_path=abox_poison,
            report_path=run_dir / "reasoner_poisoned.json",
        )
    failed_while_poisoned = bool(abox_poison_build.get("ok")) and bool(
        reasoner_poison and not reasoner_poison.get("ok")
    )
    _log(
        f"[prove] semantic poisoned={poisoned} "
        f"failed_while_poisoned={failed_while_poisoned}"
    )

    feedback_text = package_semantic_feedback(
        abox_build=abox_poison_build,
        reasoner=reasoner_poison,
        coverage=list(
            fixture.get("coverage")
            or _tbox_fixture_inventory(context)["all_class_locals"]
        ),
        ordering_property=ordering_prop,
        top_entity_local=_top_entity_local(context),
    )
    (run_dir / "semantic_feedback.md").write_text(feedback_text, encoding="utf-8")

    abox_healed_build: dict[str, Any] = {"ok": False}
    reasoner_healed: dict[str, Any] | None = None
    semantic_repairs: list[dict[str, Any]] = []
    healed_by_semantic: str | None = None

    if failed_while_poisoned:
        semantic_repairs = apply_semantic_feedback_repairs(
            context=context,
            feedback_text=feedback_text,
            model=model,
            max_repairs=max(1, max_ruff_repairs),
            allow_llm=True,
        )
        abox_healed = run_dir / "abox_healed.ttl"
        abox_healed_build = run_mcp_harness(
            scripts_dir=scripts_dir, fixture=fixture, abox_path=abox_healed
        )
        if abox_healed_build.get("ok"):
            reasoner_healed = run_reasoner_gate(
                tbox_paths=tbox_paths,
                abox_path=abox_healed,
                report_path=run_dir / "reasoner_healed.json",
            )
        entities_text = (
            scripts_dir / _entities_filename(context.ontology.name)
        ).read_text(encoding="utf-8")
        llm_patched = any(
            bool(item.get("llm_calls"))
            and bool(item.get("goal_met"))
            and bool(item.get("ok"))
            for item in semantic_repairs
        )
        if (
            abox_healed_build.get("ok")
            and reasoner_healed
            and reasoner_healed.get("ok")
            and SEMANTIC_POISON_PROP not in entities_text
            and llm_patched
        ):
            healed_by_semantic = "llm_patch"

    healed = bool(
        failed_while_poisoned
        and healed_by_semantic == "llm_patch"
        and abox_healed_build.get("ok")
        and reasoner_healed
        and reasoner_healed.get("ok")
    )
    proof["semantic_proof"] = {
        "injected": bool(poisoned),
        "failed_while_poisoned": failed_while_poisoned,
        "healed": healed,
        "healed_by": healed_by_semantic if healed else None,
        "semantic_repairs": semantic_repairs,
        "poison_failures": None
        if reasoner_poison is None
        else reasoner_poison.get("failures"),
        "healed_failures": None
        if reasoner_healed is None
        else reasoner_healed.get("failures"),
    }
    _write_json(run_dir / "semantic_proof.json", proof["semantic_proof"])

    overall_ok = (
        bool(proof["level1_proof"].get("healed"))
        and bool(proof["baseline"].get("ok"))
        and bool(proof["semantic_proof"].get("healed"))
    )
    summary = {
        "ok": overall_ok,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "mode": "prove_repairs",
        "ontology": ONTOLOGY_NAME,
        "hermit_required": True,
        "repair_policy": "llm_patch_only",
        "proof": proof,
    }
    _write_json(run_dir / "summary.json", summary)
    _log(
        f"[prove done] ok={overall_ok} "
        f"L1={proof['level1_proof'].get('healed_by')} "
        f"L2={proof['semantic_proof'].get('healed_by')} "
        f"summary={run_dir / 'summary.json'}"
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "OntoSynthesis main-only semantic MCP loop "
            "(regenerate → Level-1 → harness A-Box → HermiT; optional repair proofs)."
        )
    )
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Sandbox root for run directories.",
    )
    parser.add_argument(
        "--meta-task-config",
        default=str(DEFAULT_META_TASK),
        help="OntoSynthesis meta-task config.",
    )
    parser.add_argument(
        "--tbox",
        action="append",
        default=[],
        help="T-Box TTL path (repeatable). Defaults to OntoSyn + subgraphs + OM2.",
    )
    parser.add_argument("--max-outer", type=int, default=2, help="Outer semantic regenerate budget.")
    parser.add_argument(
        "--max-ruff-repairs",
        type=int,
        default=2,
        help="Level-1 / semantic LLM repair attempts per file/round.",
    )
    parser.add_argument(
        "--model",
        default="gpt-5",
        help="LLM model for full T-Box mock fixture generation and repairs.",
    )
    parser.add_argument(
        "--generation-model",
        default="gpt-5",
        help="LLM model for Coding/Prompt agent MCP script generation (default: gpt-5).",
    )
    parser.add_argument(
        "--llm-agent-generation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use LLM agents over deterministic scaffolds when regenerating scripts/prompts.",
    )
    parser.add_argument(
        "--max-agent-rounds",
        type=int,
        default=2,
        help="Max Coding/Prompt/Validation agent rounds per regenerate.",
    )
    parser.add_argument(
        "--fixture",
        help="Path to canned fixture JSON (skips LLM full T-Box mock generation).",
    )
    parser.add_argument(
        "--abox-mode",
        choices=["harness", "react"],
        default="harness",
        help="Build the scored A-Box directly from gold hints or through runtime prompts.",
    )
    parser.add_argument(
        "--enhance-prompts",
        action="store_true",
        help=(
            "Score mock-document content in react mode and feed mismatches to the "
            "next prompt-agent regeneration."
        ),
    )
    parser.add_argument(
        "--content-f1-threshold",
        type=float,
        default=0.95,
        help="Minimum gold-vs-predicted hint F1 accepted by prompt enhancement.",
    )
    parser.add_argument(
        "--graph-f1-threshold",
        type=float,
        default=0.0,
        help="Minimum oracle-vs-predicted graph F1 accepted by prompt enhancement.",
    )
    parser.add_argument(
        "--evaluation-repeats",
        type=int,
        default=1,
        help="Independent ReAct/HermiT evaluations per prompt candidate.",
    )
    parser.add_argument(
        "--scripts-source",
        help=(
            "Optional existing scripts/ontosynthesis directory to copy instead of "
            "regenerating on the first iteration / prove baseline."
        ),
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help=(
            "Disable LLM (fixture smoke only). Incompatible with --prove-repairs "
            "and exercise-* flags (non-trivial defects require LLM patches)."
        ),
    )
    parser.add_argument(
        "--exercise-level1-fail",
        action="store_true",
        help="On outer=0 before Level-1, inject a syntax defect (LLM repair required).",
    )
    parser.add_argument(
        "--exercise-semantic-fail",
        action="store_true",
        help=(
            "On outer=0 after Level-1, poison the contract ordering property "
            "so the reasoner gate fails; heal via LLM in-place patch (required)."
        ),
    )
    parser.add_argument(
        "--prove-repairs",
        action="store_true",
        help=(
            "Inject non-trivial Level-1 (syntax) and Level-2 (unknown property) defects "
            "and accept heal only via LLM unified-diff patches."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print summary JSON to stdout.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    allow_llm = not args.no_llm
    fixture_path = Path(args.fixture) if args.fixture else None
    scripts_source = Path(args.scripts_source) if args.scripts_source else None
    tbox_paths = _resolve_tbox_paths(list(args.tbox) or None)
    if args.enhance_prompts and args.abox_mode != "react":
        print("error: --enhance-prompts requires --abox-mode react", file=sys.stderr)
        return 2
    if args.enhance_prompts and not allow_llm:
        print("error: --enhance-prompts requires LLM prompt adjustment", file=sys.stderr)
        return 2

    if args.prove_repairs or args.exercise_semantic_fail or args.exercise_level1_fail:
        if not allow_llm:
            print(
                "error: non-trivial repair exercises require LLM "
                "(omit --no-llm for --prove-repairs / --exercise-*)",
                file=sys.stderr,
            )
            return 2

    if args.prove_repairs:
        if fixture_path is None:
            print("error: --prove-repairs requires --fixture", file=sys.stderr)
            return 2
        summary = run_prove_repairs(
            output_root=Path(args.output_root),
            meta_task_config=Path(args.meta_task_config),
            tbox_paths=tbox_paths,
            fixture_path=fixture_path,
            model=args.model,
            max_ruff_repairs=max(0, args.max_ruff_repairs),
            allow_llm=allow_llm,
            scripts_source=scripts_source,
        )
    else:
        if not allow_llm and fixture_path is None:
            print("error: --no-llm requires --fixture", file=sys.stderr)
            return 2
        summary = run_outer_loop(
            output_root=Path(args.output_root),
            meta_task_config=Path(args.meta_task_config),
            tbox_paths=tbox_paths,
            max_outer=max(1, args.max_outer),
            max_ruff_repairs=max(0, args.max_ruff_repairs),
            model=args.model,
            fixture_path=fixture_path,
            allow_llm=allow_llm,
            exercise_semantic=bool(args.exercise_semantic_fail),
            exercise_level1=bool(args.exercise_level1_fail),
            scripts_source=scripts_source,
            llm_agent_generation=bool(args.llm_agent_generation),
            generation_model=str(args.generation_model),
            max_agent_rounds=max(1, args.max_agent_rounds),
            abox_mode=str(args.abox_mode),
            enhance_prompts=bool(args.enhance_prompts),
            content_f1_threshold=max(0.0, min(1.0, args.content_f1_threshold)),
            graph_f1_threshold=max(0.0, min(1.0, args.graph_f1_threshold)),
            evaluation_repeats=max(1, args.evaluation_repeats),
        )

    if args.json:
        sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
