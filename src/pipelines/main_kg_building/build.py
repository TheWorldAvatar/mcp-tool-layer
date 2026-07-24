"""
Main KG Building Module

Handles knowledge graph building for iterations 2, 3, and 4.
Uses BaseAgent with MCP tools to build TTL files from extraction hints.
"""

import os
import json
import ast
import asyncio
import importlib.util
import shutil
import sys
import tempfile
import hashlib
import re
import types
import unicodedata
from datetime import datetime
from pathlib import Path
from filelock import FileLock
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from rdflib import Graph, URIRef, Literal, Namespace
from rdflib.namespace import RDF, RDFS

from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from src.utils.extraction_models import get_extraction_model
from src.pipelines.utils.ttl_publisher import (
    enforce_published_graph_hygiene_file,
    get_main_ontology_name,
    get_output_naming_config,
    load_meta_task_config,
    publish_top_ttl,
    publish_ttl,
    _normalize_medical_composite_graph,
)
from src.pipelines.utils.ordered_member_integrity import (
    enforce_ordered_member_integrity_file,
    load_all_runtime_ordered_member_profiles,
)

if TYPE_CHECKING:
    from models.BaseAgent import BaseAgent

logger = get_logger("pipeline", "MainKGBuilding")
OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")


def _get_base_agent():
    """Import BaseAgent lazily so non-agent tooling can run without langgraph installed."""
    from models.BaseAgent import BaseAgent

    return BaseAgent


def _get_runtime_policies(meta_cfg: dict) -> dict:
    return ((meta_cfg or {}).get("ontologies", {}).get("main", {}) or {}).get(
        "runtime_policies", {}
    ) or {}


def _get_main_entity_kg_policy(meta_cfg: dict) -> dict:
    return _get_runtime_policies(meta_cfg).get("main_entity_kg", {}) or {}


def _local_name(iri: str) -> str:
    text = str(iri or "").strip()
    if not text:
        return ""
    for sep in ("#", "/"):
        if sep in text:
            text = text.rsplit(sep, 1)[-1]
    return text


def _namespace_iri(iri: str) -> str:
    text = str(iri or "").strip()
    if not text:
        return ""
    for sep in ("#", "/"):
        if sep in text:
            head, tail = text.rsplit(sep, 1)
            if tail:
                return f"{head}{sep}"
    return ""


def _first_label(g: Graph, node: URIRef) -> str:
    for value in g.objects(node, RDFS.label):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalize_entity_label_key(
    text: str, suffixes_to_strip: Optional[list[str]] = None
) -> str:
    raw = unicodedata.normalize("NFKC", str(text or "")).strip()
    bracket_match = re.match(r"^[A-Za-z][A-Za-z0-9_]*-\d+\s+\[(.+)\]\s*$", raw)
    if bracket_match:
        raw = bracket_match.group(1).strip()
    normalized = raw.lower()
    greek_names = {
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
    }
    for symbol, name in greek_names.items():
        normalized = normalized.replace(symbol, name)
    for token in suffixes_to_strip or []:
        token = str(token or "").lower()
        if token and not token.startswith(" "):
            token = f" {token}"
        if normalized.endswith(token):
            normalized = normalized[: -len(token)]
    normalized = normalized.replace("·", "").replace("•", "").replace(".", "")
    normalized = re.sub(r"[^a-z0-9]+", "", normalized)
    return normalized


def _resolve_expected_top_entity_uri(
    g: Graph,
    *,
    top_class_iri: str,
    entity_uri: str = "",
    entity_label: str = "",
    label_key_suffixes_to_strip: Optional[list[str]] = None,
) -> str:
    """Resolve the canonical top entity URI from the current graph."""
    class_iri = str(top_class_iri or "").strip()
    typed_entities = [
        s
        for s in g.subjects(RDF.type, URIRef(class_iri))
        if class_iri and isinstance(s, URIRef)
    ]
    if not typed_entities:
        return str(entity_uri or "").strip()

    explicit_uri = str(entity_uri or "").strip()
    if explicit_uri:
        explicit_ref = URIRef(explicit_uri)
        if explicit_ref in typed_entities:
            return explicit_uri

    label_keys: list[str] = []
    if entity_label:
        label_key = _normalize_entity_label_key(
            entity_label, label_key_suffixes_to_strip
        )
        if label_key:
            label_keys.append(label_key)
    if explicit_uri:
        explicit_label_key = _normalize_entity_label_key(
            _first_label(g, URIRef(explicit_uri)), label_key_suffixes_to_strip
        )
        if explicit_label_key and explicit_label_key not in label_keys:
            label_keys.append(explicit_label_key)

    for label_key in label_keys:
        matched = [
            node
            for node in typed_entities
            if _normalize_entity_label_key(
                _first_label(g, node), label_key_suffixes_to_strip
            )
            == label_key
        ]
        if matched:
            chosen = _choose_preferred_typed_target(g, matched)
            if chosen is not None:
                return str(chosen)

    if len(typed_entities) == 1:
        return str(typed_entities[0])
    return explicit_uri


def _load_top_shell_graph(
    doi_folder: str, *, ontology_name: str, meta_cfg: dict
) -> Optional[Graph]:
    naming = get_output_naming_config(meta_cfg=meta_cfg, ontology_name=ontology_name)
    candidates = [
        os.path.join(doi_folder, "iteration_1.ttl"),
        os.path.join(doi_folder, "memory", "top.ttl"),
        os.path.join(doi_folder, naming.output_dir, naming.top_ttl_name),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            g = Graph()
            g.parse(path, format="turtle")
            return g
        except Exception as exc:
            logger.warning(f"Failed to parse top shell graph from {path}: {exc}")
    return None


def _ensure_published_top_shell(
    *,
    doi_hash: str,
    doi_folder: str,
    ontology_name: str,
    meta_cfg: dict,
    data_dir: str,
) -> Optional[str]:
    """Republish the top shell TTL when downstream output was cleaned."""
    naming = get_output_naming_config(meta_cfg=meta_cfg, ontology_name=ontology_name)
    published_top_ttl = os.path.join(doi_folder, naming.output_dir, naming.top_ttl_name)
    if os.path.exists(published_top_ttl):
        return published_top_ttl

    return publish_top_ttl(
        doi_hash=doi_hash,
        ontology_name=ontology_name,
        data_dir=data_dir,
        meta_cfg=meta_cfg,
        src_candidates=[
            os.path.join(doi_folder, "iteration_1.ttl"),
            os.path.join(doi_folder, "memory", "top.ttl"),
        ],
    )


def _canonicalize_top_entities(
    *,
    top_entities: list,
    doi_folder: str,
    ontology_name: str,
    meta_cfg: dict,
) -> list:
    shell_validation = (
        _get_main_entity_kg_policy(meta_cfg).get("shell_validation", {}) or {}
    )
    top_class_iri = str(shell_validation.get("top_entity_class_iri") or "").strip()
    label_key_suffixes = shell_validation.get("label_key_suffixes_to_strip") or []
    if not isinstance(label_key_suffixes, list):
        label_key_suffixes = []
    if not top_class_iri or not isinstance(top_entities, list) or not top_entities:
        return top_entities

    shell_graph = _load_top_shell_graph(
        doi_folder, ontology_name=ontology_name, meta_cfg=meta_cfg
    )
    if shell_graph is None:
        return top_entities

    normalized_entities: list[dict] = []
    for entity in top_entities:
        if not isinstance(entity, dict):
            normalized_entities.append(entity)
            continue
        updated = dict(entity)
        resolved_uri = _resolve_expected_top_entity_uri(
            shell_graph,
            top_class_iri=top_class_iri,
            entity_uri=str(entity.get("uri") or ""),
            entity_label=str(entity.get("label") or ""),
            label_key_suffixes_to_strip=label_key_suffixes,
        )
        if resolved_uri and resolved_uri != str(entity.get("uri") or ""):
            logger.warning(
                "Canonicalized top entity URI for '%s': %s -> %s",
                str(entity.get("label") or ""),
                str(entity.get("uri") or ""),
                resolved_uri,
            )
            updated["uri"] = resolved_uri
        normalized_entities.append(updated)
    return normalized_entities


def _mint_top_entity_iri(label: str, top_class_iri: str = "") -> str:
    digest = hashlib.sha1(str(label or "").strip().encode("utf-8")).hexdigest()
    class_local = _local_name(top_class_iri) or "TopEntity"
    return f"https://www.theworldavatar.com/kg/instance/{class_local}/{digest}"


def _top_entities_from_txt(doi_folder: str, top_class_iri: str = "") -> list[dict]:
    txt_path = os.path.join(doi_folder, "top_entities.txt")
    if not os.path.exists(txt_path):
        return []
    try:
        with open(txt_path, "r", encoding="utf-8") as f:
            lines = [line.strip(" \t\r\n-•") for line in f if line.strip()]
    except Exception:
        return []

    entities: list[dict] = []
    seen: set[str] = set()
    for label in lines:
        bracket_match = re.match(r"^[A-Za-z][A-Za-z0-9_]*-\d+\s+\[(.+)\]\s*$", label)
        if bracket_match:
            label = bracket_match.group(1)
        class_local = _local_name(top_class_iri)
        if class_local:
            label = re.sub(
                rf"^\s*(?:{re.escape(class_local)}\s*[—:-]\s*)",
                "",
                label,
                flags=re.IGNORECASE,
            ).strip()
        key = _normalize_entity_label_key(label)
        if not label or not key or key in seen:
            continue
        seen.add(key)
        entities.append(
            {
                "uri": _mint_top_entity_iri(label, top_class_iri),
                "label": label,
                "types": [],
            }
        )
    return entities


def _supplement_top_entities_from_txt(
    doi_folder: str,
    top_entities: list,
    top_class_iri: str = "",
) -> list:
    txt_entities = _top_entities_from_txt(doi_folder, top_class_iri)
    if not txt_entities:
        return top_entities or []
    merged: list[dict] = []
    seen: set[str] = set()
    for entity in (top_entities or []) + txt_entities:
        if not isinstance(entity, dict):
            continue
        key = _normalize_entity_label_key(str(entity.get("label") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(entity)
    return merged


def _drop_nodes_as_subjects(g: Graph, nodes: List[URIRef]) -> Graph:
    if not nodes:
        return g
    rewritten = Graph()
    for prefix, ns in g.namespaces():
        rewritten.bind(prefix, ns)
    node_set = set(nodes)
    for s, p, o in g:
        if s in node_set:
            continue
        rewritten.add((s, p, o))
    return rewritten


def _choose_repair_target_for_top_entity(
    g: Graph,
    *,
    top_entity: URIRef,
    target_cls: URIRef,
    label_key_suffixes_to_strip: Optional[list[str]] = None,
) -> Optional[URIRef]:
    """
    Choose a conservative candidate to satisfy a required link.

    Preference order:
    1. exact label-key match to the top entity label after configured normalization
    2. the only typed target if there is exactly one
    3. no guess when multiple unrelated targets exist
    """
    typed_targets = sorted(
        {s for s in g.subjects(RDF.type, target_cls) if isinstance(s, URIRef)}, key=str
    )
    if not typed_targets:
        return None

    top_label_key = _normalize_entity_label_key(
        _first_label(g, top_entity), label_key_suffixes_to_strip
    )
    if top_label_key:
        matched = [
            node
            for node in typed_targets
            if _normalize_entity_label_key(
                _first_label(g, node), label_key_suffixes_to_strip
            )
            == top_label_key
        ]
        if matched:
            return _choose_preferred_typed_target(g, matched)

    if len(typed_targets) == 1:
        return typed_targets[0]
    return None


def _derive_placeholder_label_from_top_label(
    text: str, *, strip_suffix: str = ""
) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or "")).strip()
    suffix = str(strip_suffix or "").strip()
    if suffix and normalized.lower().endswith(suffix.lower()):
        return normalized[: -len(suffix)].strip()
    return normalized


def _resolve_hint_property_iri(
    *, property_namespace_iri: str, prop_name: str
) -> URIRef:
    text = str(prop_name or "").strip()
    if not text:
        return URIRef(property_namespace_iri)
    if text.startswith("http://") or text.startswith("https://"):
        return URIRef(text)
    local = text.rsplit(":", 1)[-1] if ":" in text and "://" not in text else text
    return URIRef(f"{property_namespace_iri}{local}")


def _materialize_placeholder_target(
    g: Graph,
    *,
    target_cls: URIRef,
    label: str,
) -> URIRef:
    local_name = _local_name(str(target_cls)) or "Thing"
    digest = hashlib.sha1(f"{local_name}:{label}".encode("utf-8")).hexdigest()
    node = URIRef(f"https://www.theworldavatar.com/kg/instance/{local_name}/{digest}")
    g.add((node, RDF.type, target_cls))
    g.add((node, RDFS.label, Literal(label)))
    return node


def _looks_placeholder_label(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    markers = (
        "placeholder",
        "not specified",
        "no source text",
        "no details",
    )
    return any(marker in lowered for marker in markers)


def _is_placeholder_target(g: Graph, node: URIRef) -> bool:
    return _looks_placeholder_label(_first_label(g, node))


def _first_integer_object(g: Graph, node: URIRef, pred: URIRef) -> Optional[int]:
    for value in g.objects(node, pred):
        try:
            return int(str(value))
        except Exception:
            continue
    return None


def _preferred_ordered_member_targets(g: Graph, *, target_cls: URIRef) -> list[URIRef]:
    """Choose one concrete node per semantic member, preferring richer subclassed nodes."""
    order_pred = URIRef(f"{_namespace_iri(str(target_cls))}hasOrder")
    typed_targets = sorted(
        {s for s in g.subjects(RDF.type, target_cls) if isinstance(s, URIRef)}, key=str
    )
    concrete_targets = [
        node for node in typed_targets if not _is_placeholder_target(g, node)
    ]
    if not concrete_targets:
        return []

    grouped: dict[tuple[str, Optional[int]], list[URIRef]] = {}
    for node in concrete_targets:
        label_key = _normalize_entity_label_key(_first_label(g, node)) or str(node)
        order_value = _first_integer_object(g, node, order_pred)
        grouped.setdefault((label_key, order_value), []).append(node)

    preferred: list[URIRef] = []
    for key in sorted(
        grouped.keys(), key=lambda item: (item[1] is None, item[1], item[0])
    ):
        chosen = _choose_preferred_typed_target(g, grouped[key])
        if chosen is not None:
            preferred.append(chosen)
    return preferred


def _get_hint_reconciliation_specs(main_entity_policy: dict) -> list[dict]:
    shell_validation = (main_entity_policy or {}).get("shell_validation", {}) or {}
    publish_policy = (main_entity_policy or {}).get("publish", {}) or {}
    hint_reconciliation = publish_policy.get("hint_reconciliation", {}) or {}
    optional_links = hint_reconciliation.get("optional_links", []) or []

    specs: list[dict] = []
    for raw_spec, optional in (
        *((spec, False) for spec in (shell_validation.get("required_links", []) or [])),
        *((spec, True) for spec in optional_links),
    ):
        pred_iri = str((raw_spec or {}).get("predicate_iri") or "").strip()
        target_class_iri = str((raw_spec or {}).get("target_class_iri") or "").strip()
        if not (pred_iri and target_class_iri):
            continue
        specs.append(
            {
                "section_name": str((raw_spec or {}).get("section_name") or "").strip()
                or _local_name(target_class_iri),
                "predicate_iri": pred_iri,
                "target_class_iri": target_class_iri,
                "property_namespace_iri": str(
                    (raw_spec or {}).get("property_namespace_iri") or ""
                ).strip()
                or _namespace_iri(target_class_iri)
                or _namespace_iri(pred_iri),
                "optional": optional,
                "ordered_member": bool((raw_spec or {}).get("ordered_member")),
                "prune_unhinted_scalar_properties": bool(
                    (raw_spec or {}).get("prune_unhinted_scalar_properties")
                ),
            }
        )
    return specs


def _get_hint_exclusive_property_groups(main_entity_policy: dict) -> list[dict]:
    publish_policy = (main_entity_policy or {}).get("publish", {}) or {}
    hint_reconciliation = publish_policy.get("hint_reconciliation", {}) or {}
    raw_groups = hint_reconciliation.get("mutually_exclusive_property_groups", []) or []
    groups: list[dict] = []
    for raw_group in raw_groups:
        target_class_iri = str((raw_group or {}).get("target_class_iri") or "").strip()
        property_iris = [
            str(item or "").strip()
            for item in ((raw_group or {}).get("property_iris") or [])
            if str(item or "").strip()
        ]
        if target_class_iri and property_iris:
            groups.append(
                {
                    "target_class_iri": target_class_iri,
                    "property_iris": property_iris,
                }
            )
    return groups


def _hint_key_variants(*values: str) -> set[str]:
    variants: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        tokens = {text, _local_name(text)}
        if ":" in text and "://" not in text:
            tokens.add(text.rsplit(":", 1)[-1])
        for token in tokens:
            raw = str(token or "").strip()
            normalized = raw.lower()
            if normalized:
                variants.add(normalized)
                compact = re.sub(r"[^a-z0-9]+", "", normalized)
                if compact:
                    variants.add(compact)
            numbered_base = re.sub(r"\s+\d+$", "", raw).strip().lower()
            if numbered_base:
                variants.add(numbered_base)
                numbered_compact = re.sub(r"[^a-z0-9]+", "", numbered_base)
                if numbered_compact:
                    variants.add(numbered_compact)
            paren_base = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip().lower()
            if paren_base:
                variants.add(paren_base)
                paren_compact = re.sub(r"[^a-z0-9]+", "", paren_base)
                if paren_compact:
                    variants.add(paren_compact)
            paren_numbered_base = re.sub(r"\s+\d+$", "", paren_base).strip().lower()
            if paren_numbered_base:
                variants.add(paren_numbered_base)
    return variants


def _matches_hint_key(key: Any, expected_variants: set[str]) -> bool:
    if not expected_variants:
        return False
    return bool(_hint_key_variants(str(key or "")) & expected_variants)


def _coerce_hint_kv_line(value: Any) -> Optional[dict]:
    """Coerce generic string hint lines like ``prefix:field = value`` into a dict."""
    text = str(value or "").strip().lstrip("-").strip()
    if not text or text.lower().startswith("warning:"):
        return None
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_:\-.]*)\s*(?:=|:)\s*(.+?)\s*$", text)
    if not match:
        return None
    key = match.group(1).strip()
    val = match.group(2).strip().strip('"')
    if (
        not key
        or not val
        or val.lower() in {"not provided", "unknown", "n/a", "na", "-"}
    ):
        return None
    return {key: val}


def _coerce_hinted_section_payload(
    payload: Any, *, target_variants: set[str]
) -> Optional[dict]:
    if isinstance(payload, list):
        merged: dict = {}
        found = False
        for item in payload:
            coerced = (
                _coerce_hint_kv_line(item)
                if not isinstance(item, (dict, list))
                else _coerce_hinted_section_payload(
                    item, target_variants=target_variants
                )
            )
            if isinstance(coerced, dict):
                merged = _merge_hint_payloads(merged, coerced)
                found = True
        return merged if found else None

    if not isinstance(payload, (dict, list)):
        return _coerce_hint_kv_line(payload)

    if not isinstance(payload, dict):
        return None

    unwrapped: dict = {}
    has_unwrapped = False
    for key, value in payload.items():
        if _matches_hint_key(key, target_variants):
            coerced = _coerce_hinted_section_payload(
                value, target_variants=target_variants
            )
            if isinstance(coerced, dict):
                unwrapped = _merge_hint_payloads(unwrapped, coerced)
                has_unwrapped = True
    if has_unwrapped:
        return unwrapped

    return dict(payload)


def _collect_matching_hinted_sections(
    payload: Any, *, section_variants: set[str]
) -> list[Any]:
    matches: list[Any] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if _matches_hint_key(key, section_variants):
                matches.append(value)
            matches.extend(
                _collect_matching_hinted_sections(
                    value, section_variants=section_variants
                )
            )
    elif isinstance(payload, list):
        for item in payload:
            matches.extend(
                _collect_matching_hinted_sections(
                    item, section_variants=section_variants
                )
            )
    return matches


def _extract_hinted_section_payload(
    aggregated_hints: dict, spec: dict
) -> Optional[dict]:
    if not isinstance(aggregated_hints, dict):
        return None

    section_variants = _hint_key_variants(
        str(spec.get("section_name") or ""),
        str(spec.get("predicate_iri") or ""),
        _local_name(str(spec.get("predicate_iri") or "")),
        str(spec.get("target_class_iri") or ""),
        _local_name(str(spec.get("target_class_iri") or "")),
    )
    target_variants = _hint_key_variants(
        str(spec.get("target_class_iri") or ""),
        _local_name(str(spec.get("target_class_iri") or "")),
    )
    if not section_variants:
        return None

    merged: dict = {}
    found = False
    for candidate in _collect_matching_hinted_sections(
        aggregated_hints, section_variants=section_variants
    ):
        coerced = _coerce_hinted_section_payload(
            candidate, target_variants=target_variants
        )
        if isinstance(coerced, dict):
            merged = _merge_hint_payloads(merged, coerced)
            found = True
    return merged if found else None


def _apply_entity_context_runtime_env(
    *, main_entity_policy: dict, entity_safe: str
) -> None:
    """
    Export config-derived entity-context handling rules so MCP utility modules can
    keep all iter2+ writes in one consistent memory file.
    """
    runtime_context = (main_entity_policy or {}).get("runtime_context", {}) or {}
    lock_to_global = bool(runtime_context.get("lock_to_global_state_entity_name"))
    if lock_to_global:
        os.environ["TWA_MCP_ENTITY_CONTEXT_LOCK_TO_GLOBAL_STATE"] = "1"
    else:
        os.environ.pop("TWA_MCP_ENTITY_CONTEXT_LOCK_TO_GLOBAL_STATE", None)
    os.environ["TWA_MCP_ENTITY_CONTEXT_EXPECTED_NAME"] = str(entity_safe or "").strip()


def _augment_kg_prompt_with_runtime_rules(
    *,
    kg_prompt: str,
    entity_label: str,
    entity_uri: str,
    doi_hash: str,
    main_entity_policy: dict,
    hints_content: str = "",
) -> str:
    prompt_rules = (main_entity_policy or {}).get("prompt_rules", {}) or {}
    shell_validation = (main_entity_policy or {}).get("shell_validation", {}) or {}
    lines: list[str] = []

    if prompt_rules.get("require_top_entity_reuse"):
        lines.append(
            f"- Reuse the existing top-level entity IRI `{entity_uri}`. Do not replace it."
        )
    if prompt_rules.get("forbid_new_top_entity_creation"):
        lines.append("- Do not create a second top-level entity for this case.")
    if prompt_rules.get("require_required_links_before_export"):
        for spec in shell_validation.get("required_links", []) or []:
            pred_iri = str((spec or {}).get("predicate_iri") or "").strip()
            if pred_iri:
                pred_name = pred_iri.rsplit("/", 1)[-1]
                lines.append(
                    f"- Before export, ensure the top-level entity `{entity_uri}` has the required link `{pred_name}` to the relevant sub-entity."
                )
    if lines:
        lines.insert(0, "Config-derived graph integrity rules:")
        lines.append(
            f"- Use `{doi_hash}` as the document identifier/doi argument when relevant."
        )
        lines.append(f"- Keep all work scoped to entity label `{entity_label}`.")
        lines.append(
            "- Every explicit canonical field present in `ExtractedHints` must be materialized into the scoped KG before export; do not terminate with only placeholder shell entities."
        )
        lines.append(
            "- Ordered-member property fidelity is mandatory: for each hinted member with `hasOrder`, materialize each property on the member with that exact order and never copy, inherit, or reuse a property value from a different ordered member."
        )
        lines.append(
            "- If two ordered members share the same class, still treat them as separate individuals with independent property values; each value must match the corresponding hint for that member order."
        )
        lines.append(
            "- When an ordered member hint omits an optional property, do not populate that property by copying it from a previous or following member unless the prompt or T-Box explicitly states an inheritance rule for that property."
        )
        hints_text = _strip_code_fences(hints_content)
        has_ordered_member_evidence = bool(
            re.search(r"<(?:step|member)\d+>", hints_text, flags=re.IGNORECASE)
            or re.search(r"\bhasOrder\b", hints_text)
        )
        has_integrity_only_flags = bool(
            re.search(r"\bINTEGRITY_FLAGS\b", hints_text, flags=re.IGNORECASE)
        )
        if not has_ordered_member_evidence:
            lines.append(
                "- If the current `ExtractedHints` do not contain explicit ordered-member evidence (for example member tokens, per-member triples, or ordering properties), do not create or duplicate ordered child members in this iteration. Preserve previously-created graph state and only materialize the explicit facts present in this iteration's hints."
            )
        if has_integrity_only_flags:
            lines.append(
                "- Treat integrity or validation flags in `ExtractedHints` as constraints on the graph, not as standalone evidence for creating new entities or replaying previously-created members."
            )
        if re.search(r"\bhasParameter\s*:", hints_text, flags=re.IGNORECASE):
            lines.append(
                "- If `ExtractedHints` contain generic parameter lines such as "
                "`hasParameter: name=value unit`, materialize them on the same ordered member. "
                "Use the available creation-tool arguments whose local names best match the parameter "
                "name and quantity kind, especially paired `<property>_value` and `<property>_unit` arguments; "
                "do not drop a generic parameter line only because it is not already written as a concrete property."
            )
        return kg_prompt.rstrip() + "\n\n" + "\n".join(lines) + "\n"
    return kg_prompt


def _response_has_recoverable_kg_error(response_text: str) -> bool:
    lowered = (response_text or "").lower()
    recoverable_markers = (
        "recoverable_kg_error",
        "missing_subject",
        "missing subject",
        "container does not exist",
        "subject entity might not exist",
    )
    return any(marker in lowered for marker in recoverable_markers)


def _response_claims_persistence(response_text: str) -> bool:
    lowered = (response_text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "exported",
            "successfully materialized",
            "successfully created",
            "rdf graph has been successfully",
            "ttl",
        )
    )


def _has_entity_persistence_artifact(
    *, doi_folder: str, entity_safe: str, entity_label: str
) -> bool:
    mem_dir = os.path.join(doi_folder, "memory")
    candidates = [
        os.path.join(mem_dir, f"{entity_safe}.ttl"),
        os.path.join(mem_dir, f"{entity_safe.lower()}.ttl"),
        os.path.join(mem_dir, f"{entity_label}.ttl"),
    ]
    if any(os.path.exists(path) for path in candidates):
        return True
    exports_dir = os.path.join(doi_folder, "exports")
    if os.path.isdir(exports_dir):
        for name in os.listdir(exports_dir):
            if name.startswith(entity_safe) and name.endswith(".ttl"):
                return True
    return False


def _build_kg_recovery_prompt(
    *, base_prompt: str, entity_label: str, entity_uri: str
) -> str:
    return (
        base_prompt.rstrip()
        + "\n\nRecovery instructions for this retry:\n"
        + f"- A previous attempt for `{entity_label}` failed because it did not leave a persisted entity TTL artifact.\n"
        + "- You must use MCP tools in this retry. Do not answer in prose until after the export tool succeeds.\n"
        + f"- First call `init_memory` with the document id and top-level entity name `{entity_label}`.\n"
        + f"- Then create or reuse the scoped top-level entity `{entity_uri}` before creating or linking child entities.\n"
        + "- Re-apply every explicit canonical field from `ExtractedHints` by calling the matching `create_*` and `add_*` tools.\n"
        + "- For ordered members, create each hinted member with its order scalar and link each member individually to the scoped top entity.\n"
        + "- Finally call `export_memory`. If `export_memory` does not return TTL content or success, do not claim success.\n"
        + "- Do not switch scope and do not create a second competing top-level entity.\n"
    )


def _validate_entity_ttl_structure(
    *,
    ttl_path: str,
    entity_uri: str,
    entity_label: str,
    main_entity_policy: dict,
) -> tuple[bool, list[str]]:
    """
    Validate the published entity TTL against config-driven shell/link expectations.
    """
    shell_validation = (main_entity_policy or {}).get("shell_validation", {}) or {}
    messages: list[str] = []
    if not ttl_path or not os.path.exists(ttl_path):
        return False, [f"TTL not found for validation: {ttl_path}"]

    try:
        g = Graph()
        g.parse(ttl_path, format="turtle")
    except Exception as e:
        return False, [f"Failed to parse TTL: {e}"]

    top_class_iri = str(shell_validation.get("top_entity_class_iri") or "").strip()
    label_key_suffixes = shell_validation.get("label_key_suffixes_to_strip") or []
    if not isinstance(label_key_suffixes, list):
        label_key_suffixes = []
    resolved_entity_uri = _resolve_expected_top_entity_uri(
        g,
        top_class_iri=top_class_iri,
        entity_uri=entity_uri,
        entity_label=entity_label,
        label_key_suffixes_to_strip=label_key_suffixes,
    )
    top_entity = URIRef(resolved_entity_uri) if resolved_entity_uri else None
    if top_entity is not None and shell_validation.get("require_entity_uri_subject"):
        if not any(g.triples((top_entity, None, None))):
            messages.append(
                f"Missing top-level entity subject in TTL: {resolved_entity_uri or entity_uri}"
            )
        elif top_class_iri and (top_entity, RDF.type, URIRef(top_class_iri)) not in g:
            messages.append(
                f"Top-level entity missing required rdf:type: {top_class_iri}"
            )

    if top_entity is not None:
        for spec in shell_validation.get("required_links", []) or []:
            pred_iri = str((spec or {}).get("predicate_iri") or "").strip()
            target_class_iri = str((spec or {}).get("target_class_iri") or "").strip()
            min_count = int((spec or {}).get("min_count") or 0)
            if not pred_iri:
                continue
            pred = URIRef(pred_iri)
            objs = [o for o in g.objects(top_entity, pred) if isinstance(o, URIRef)]
            if target_class_iri:
                target_cls = URIRef(target_class_iri)
                objs = [o for o in objs if (o, RDF.type, target_cls) in g]
            if len(objs) < min_count:
                messages.append(
                    f"Missing required link {pred_iri}: expected >= {min_count}, found {len(objs)}"
                )

    return (len(messages) == 0), messages


def _warn_typed_nodes_missing_configured_predicates(
    *,
    ttl_path: str,
    entity_label: str,
    warning_rules: list[dict],
) -> None:
    """Log configured warnings for typed nodes missing required predicates."""
    if not warning_rules:
        return
    if not ttl_path or not os.path.exists(ttl_path):
        return
    try:
        g = Graph()
        g.parse(ttl_path, format="turtle")
    except Exception:
        return

    for raw_rule in warning_rules:
        if not isinstance(raw_rule, dict):
            continue
        subject_class_iri = str(raw_rule.get("subject_class_iri") or "").strip()
        predicate_iri = str(raw_rule.get("predicate_iri") or "").strip()
        order_predicate_iri = str(raw_rule.get("order_predicate_iri") or "").strip()
        message = str(
            raw_rule.get("message") or "Typed node is missing configured predicate"
        ).strip()
        if not subject_class_iri or not predicate_iri:
            continue
        subject_class = URIRef(subject_class_iri)
        predicate = URIRef(predicate_iri)
        order_predicate = URIRef(order_predicate_iri) if order_predicate_iri else None
        for node in g.subjects(RDF.type, subject_class):
            if sum(1 for _ in g.objects(node, predicate)) > 0:
                continue
            order_val: Any = None
            if order_predicate is not None:
                for o in g.objects(node, order_predicate):
                    try:
                        order_val = int(o)
                    except Exception:
                        order_val = str(o)
                    break
            logger.warning(
                "    ⚠️  %s (entity=%r, order=%r, node=%s)",
                message,
                entity_label,
                order_val,
                node,
            )


def _repair_published_entity_ttl(
    *,
    ttl_path: str,
    doi_folder: str,
    ontology_name: str,
    entity_uri: str,
    entity_label: str,
    meta_cfg: dict,
    main_entity_policy: dict,
) -> tuple[bool, list[str]]:
    """
    Post-publish repair step: merge top shell into the published entity TTL and
    attach configured singleton links if missing.
    """
    publish_policy = (main_entity_policy or {}).get("publish", {}) or {}
    shell_validation = (main_entity_policy or {}).get("shell_validation", {}) or {}
    label_key_suffixes = shell_validation.get("label_key_suffixes_to_strip") or []
    if not isinstance(label_key_suffixes, list):
        label_key_suffixes = []
    if not bool(publish_policy.get("merge_top_ttl_into_entity_ttl")):
        return True, []
    if not ttl_path or not os.path.exists(ttl_path):
        return False, [f"Published TTL not found for repair: {ttl_path}"]

    naming = get_output_naming_config(meta_cfg=meta_cfg, ontology_name=ontology_name)
    top_ttl = os.path.join(doi_folder, "iteration_1.ttl")
    if not os.path.exists(top_ttl):
        top_ttl = os.path.join(doi_folder, "memory", "top.ttl")
    if not os.path.exists(top_ttl):
        top_ttl = os.path.join(doi_folder, naming.output_dir, naming.top_ttl_name)
    if not os.path.exists(top_ttl):
        return False, ["Top shell TTL not found for merge"]

    messages: list[str] = []
    try:
        g = Graph()
        g.parse(top_ttl, format="turtle")
        g.parse(ttl_path, format="turtle")
    except Exception as e:
        return False, [f"Failed to parse TTLs for repair: {e}"]

    def _choose_preferred_typed_target(typed_targets: list[URIRef]) -> URIRef | None:
        if not typed_targets:
            return None

        def _score(node: URIRef) -> tuple[int, str]:
            outgoing = sum(1 for _ in g.triples((node, None, None)))
            incoming = sum(1 for _ in g.triples((None, None, node)))
            return (outgoing + incoming, str(node))

        return sorted(typed_targets, key=_score, reverse=True)[0]

    top_class_iri = str(shell_validation.get("top_entity_class_iri") or "").strip()
    resolved_entity_uri = _resolve_expected_top_entity_uri(
        g,
        top_class_iri=top_class_iri,
        entity_uri=entity_uri,
        entity_label=entity_label,
        label_key_suffixes_to_strip=label_key_suffixes,
    )
    top_entity = URIRef(resolved_entity_uri) if resolved_entity_uri else None
    if top_class_iri and top_entity is not None:
        top_class = URIRef(top_class_iri)
        typed_top_entities = [
            s for s in g.subjects(RDF.type, top_class) if isinstance(s, URIRef)
        ]
        if top_entity in typed_top_entities and len(typed_top_entities) > 1:
            drop_nodes = [node for node in typed_top_entities if node != top_entity]
            g = _drop_nodes_as_subjects(g, drop_nodes)
            messages.append(
                f"Pruned {len(drop_nodes)} unrelated top-level shell entities during repair"
            )

    for spec in shell_validation.get("required_links", []) or []:
        pred_iri = str((spec or {}).get("predicate_iri") or "").strip()
        target_class_iri = str((spec or {}).get("target_class_iri") or "").strip()
        min_count = int((spec or {}).get("min_count") or 0)
        repair_mode = str((spec or {}).get("repair_mode") or "").strip()
        if not (top_entity and pred_iri and target_class_iri):
            continue
        pred = URIRef(pred_iri)
        target_cls = URIRef(target_class_iri)
        current_targets = [
            o
            for o in g.objects(top_entity, pred)
            if isinstance(o, URIRef) and (o, RDF.type, target_cls) in g
        ]
        if len(current_targets) >= min_count:
            continue
        if bool((spec or {}).get("ordered_member")):
            ordered_targets = _preferred_ordered_member_targets(
                g, target_cls=target_cls
            )
            if ordered_targets:
                for node in ordered_targets:
                    g.add((top_entity, pred, node))
                messages.append(
                    f"Attached required ordered-member link {pred_iri} to {len(ordered_targets)} concrete node(s)"
                )
                continue
        chosen_target: URIRef | None = None
        if repair_mode == "attach_singleton_if_missing":
            typed_targets = sorted(
                {s for s in g.subjects(RDF.type, target_cls) if isinstance(s, URIRef)},
                key=str,
            )
            chosen_target = _choose_preferred_typed_target(typed_targets)
        else:
            chosen_target = _choose_repair_target_for_top_entity(
                g,
                top_entity=top_entity,
                target_cls=target_cls,
                label_key_suffixes_to_strip=label_key_suffixes,
            )

        placeholder_cfg = (spec or {}).get("placeholder_target_if_missing") or {}
        if (
            chosen_target is None
            and isinstance(placeholder_cfg, dict)
            and placeholder_cfg
        ):
            placeholder_label = str(placeholder_cfg.get("label") or "").strip()
            if not placeholder_label and bool(
                placeholder_cfg.get("derive_label_from_top_entity")
            ):
                placeholder_label = _derive_placeholder_label_from_top_label(
                    _first_label(g, top_entity),
                    strip_suffix=str(placeholder_cfg.get("strip_suffix") or ""),
                )
            if placeholder_label:
                chosen_target = _materialize_placeholder_target(
                    g,
                    target_cls=target_cls,
                    label=placeholder_label,
                )
                messages.append(
                    f"Materialized placeholder target '{placeholder_label}' for missing required link {pred_iri}"
                )

        if chosen_target is not None:
            g.add((top_entity, pred, chosen_target))
            messages.append(f"Attached required link {pred_iri} to {chosen_target}")

    if top_entity is not None:
        for spec in _get_hint_reconciliation_specs(main_entity_policy):
            if not bool(spec.get("optional")):
                continue
            pred_iri = str(spec.get("predicate_iri") or "").strip()
            target_class_iri = str(spec.get("target_class_iri") or "").strip()
            if not (pred_iri and target_class_iri):
                continue
            pred = URIRef(pred_iri)
            target_cls = URIRef(target_class_iri)
            current_targets = [
                o
                for o in g.objects(top_entity, pred)
                if isinstance(o, URIRef) and (o, RDF.type, target_cls) in g
            ]
            if current_targets:
                continue
            typed_targets = sorted(
                {s for s in g.subjects(RDF.type, target_cls) if isinstance(s, URIRef)},
                key=str,
            )
            chosen_target = _choose_preferred_typed_target(typed_targets)
            if chosen_target is not None:
                g.add((top_entity, pred, chosen_target))
                messages.append(
                    f"Attached optional singleton {chosen_target} via {pred_iri} "
                    f"from {len(typed_targets)} candidate(s)"
                )

    try:
        g.serialize(destination=ttl_path, format="turtle")
    except Exception as e:
        return False, messages + [f"Failed to write repaired TTL: {e}"]

    return True, messages


# Generated artifacts resolver (candidate-first)
def resolve_generated_file(path: str, project_root: str = ".") -> str:
    """
    Resolve a generated artifact path.

    Prefer `ai_generated_contents_candidate/` (where generation writes in this repo),
    then fall back to `ai_generated_contents/` if present.

    Returns an absolute-ish path joined with project_root, suitable for open().
    """
    rel = (path or "").replace("\\", "/")
    candidates: list[str] = []
    override_root = (
        os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "")
        .strip()
        .replace("\\", "/")
        .rstrip("/")
    )
    strict_root = os.environ.get("TWA_REQUIRE_GENERATED_ARTIFACT_ROOT") == "1"
    if rel.startswith("ai_generated_contents/"):
        if override_root:
            candidates.append(rel.replace("ai_generated_contents", override_root, 1))
        if not strict_root:
            candidates.append(
                rel.replace("ai_generated_contents/", "ai_generated_contents_candidate/", 1)
            )
            candidates.append(rel)
    elif rel.startswith("ai_generated_contents_candidate/"):
        if override_root:
            candidates.append(
                rel.replace("ai_generated_contents_candidate", override_root, 1)
            )
        if not strict_root:
            candidates.append(rel)
            candidates.append(
                rel.replace("ai_generated_contents_candidate/", "ai_generated_contents/", 1)
            )
    else:
        candidates.append(rel)

    for c in candidates:
        full = os.path.join(project_root, c)
        if c and os.path.exists(full):
            return full
    # Default to the first candidate even if it doesn't exist (caller may log)
    if strict_root:
        raise FileNotFoundError(
            f"Required generated artifact is missing: {candidates[0]}"
        )
    return os.path.join(project_root, candidates[0])


def _artifact_is_current(
    path: str, dependency_paths: List[str] | None = None, *, project_root: str = "."
) -> bool:
    """Return True if artifact exists, is non-empty, and is not older than dependencies."""
    if not os.path.exists(path):
        return False
    try:
        if os.path.getsize(path) <= 0:
            return False
        artifact_mtime = os.path.getmtime(path)
    except Exception:
        return False

    dep_mtimes: list[float] = []
    for dep in dependency_paths or []:
        if not dep:
            continue
        dep_path = (
            dep
            if os.path.isabs(dep)
            else resolve_generated_file(dep, project_root=project_root)
        )
        if not os.path.exists(dep_path):
            return False
        try:
            dep_mtimes.append(os.path.getmtime(dep_path))
        except Exception:
            return False
    return (not dep_mtimes) or artifact_mtime >= max(dep_mtimes)


# Global state management for MCP server
GLOBAL_STATE_DIR = "data"
GLOBAL_STATE_JSON = os.path.join(GLOBAL_STATE_DIR, "global_state.json")
GLOBAL_STATE_LOCK = os.path.join(GLOBAL_STATE_DIR, "global_state.lock")


def write_global_state(
    doi: str, top_level_entity_name: str, top_level_entity_iri: str | None = None
):
    """Write global state atomically with file lock for MCP server to read."""
    os.makedirs(GLOBAL_STATE_DIR, exist_ok=True)
    lock = FileLock(GLOBAL_STATE_LOCK)
    lock.acquire(timeout=30.0)
    try:
        state = {"doi": doi, "top_level_entity_name": top_level_entity_name}
        if top_level_entity_iri:
            state["top_level_entity_iri"] = top_level_entity_iri
        fd, tmp = tempfile.mkstemp(dir=GLOBAL_STATE_DIR, suffix=".json.tmp")
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, GLOBAL_STATE_JSON)
        logger.info(f"Global state written: doi={doi}, entity={top_level_entity_name}")
    finally:
        lock.release()


def _safe_name(label: str) -> str:
    """Convert entity label to safe filename."""
    s = unicodedata.normalize("NFKC", label or "entity")
    for ch in [":", "：", "﹕", "∶", "꞉", "︰", "\uf03a"]:
        s = s.replace(ch, ":")
    s = (
        s.replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace("α", "alpha")
        .replace("β", "beta")
        .replace("γ", "gamma")
        .replace("δ", "delta")
        .replace("Α", "Alpha")
        .replace("Β", "Beta")
        .replace("Γ", "Gamma")
        .replace("Δ", "Delta")
    )
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "entity"


def _strip_export_timestamp(stem: str) -> str:
    parts = (stem or "").split("_")
    if (
        len(parts) >= 3
        and parts[-2].isdigit()
        and len(parts[-2]) == 8
        and parts[-1].isdigit()
        and len(parts[-1]) == 6
    ):
        return "_".join(parts[:-2])
    if len(parts) >= 2 and parts[-1].isdigit():
        return "_".join(parts[:-1])
    return stem


def _path_matches_entity_runtime_artifact(
    *,
    path: str,
    entity_safe: str,
    entity_uri: str = "",
    allow_iteration_suffix: bool = False,
) -> bool:
    name = os.path.basename(path)
    if not name.lower().endswith(".ttl"):
        return False
    if name.lower() == "top.ttl":
        return False

    stem = os.path.splitext(name)[0]
    normalized = _safe_name(stem)
    base_normalized = _safe_name(_strip_export_timestamp(stem))

    if normalized == entity_safe or base_normalized == entity_safe:
        return True
    if allow_iteration_suffix and stem.endswith(f"_{entity_safe}"):
        return True

    if entity_uri:
        iri_token = f"<{entity_uri}>"
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return iri_token in fh.read()
        except Exception:
            return False

    return False


def _purge_entity_runtime_artifacts(
    *,
    doi_folder: str,
    entity_label: str,
    entity_safe: str,
    entity_uri: str = "",
    ontology_output_dir: str = "",
    intermediate_ttl_dir: str = "",
) -> int:
    """
    Remove stale per-entity publish/intermediate artifacts before a clean KG rebuild.

    Important: keep canonical MCP runtime persistence (`memory/` and `exports/`) intact,
    because the current MCP flow still relies on those locations as the authoritative
    write-back surface during the active run. We only clear derived/published artifacts
    that can safely be regenerated from the current run.
    """
    deleted = 0
    candidate_dirs = [
        intermediate_ttl_dir,
    ]
    if ontology_output_dir:
        candidate_dirs.append(ontology_output_dir)

    seen: set[str] = set()
    for directory in candidate_dirs:
        if not directory or not os.path.isdir(directory):
            continue
        try:
            for fn in os.listdir(directory):
                path = os.path.join(directory, fn)
                key = os.path.normcase(os.path.abspath(path))
                if key in seen or not os.path.isfile(path):
                    continue
                seen.add(key)
                if not _path_matches_entity_runtime_artifact(
                    path=path,
                    entity_safe=entity_safe,
                    entity_uri=entity_uri,
                    allow_iteration_suffix=(
                        os.path.normcase(os.path.abspath(directory))
                        == os.path.normcase(os.path.abspath(intermediate_ttl_dir))
                    )
                    if intermediate_ttl_dir
                    else False,
                ):
                    continue
                try:
                    os.remove(path)
                    deleted += 1
                except Exception as e:
                    logger.warning(
                        f"    ⚠️  Failed to remove stale entity artifact {path}: {e}"
                    )
        except Exception as e:
            logger.warning(
                f"    ⚠️  Failed to scan runtime artifacts in {directory} for {entity_label}: {e}"
            )

    if deleted:
        logger.info(
            f"    🧹 Removed {deleted} stale runtime artifact(s) for {entity_label}"
        )
    return deleted


def _purge_entity_canonical_persistence(
    *,
    doi_folder: str,
    entity_label: str,
    entity_safe: str,
    entity_uri: str = "",
) -> int:
    """
    Remove stale per-entity canonical persistence before a fresh multi-iteration KG rebuild.

    We intentionally keep `top.ttl` intact and only clear entity-scoped `memory/` / `exports/`
    files that match the current entity. This prevents old runs from leaking outdated
    sub-entity variants back into the new iteration chain.
    """
    deleted = 0
    candidate_dirs = [
        os.path.join(doi_folder, "memory"),
        os.path.join(doi_folder, "exports"),
    ]

    seen: set[str] = set()
    for directory in candidate_dirs:
        if not os.path.isdir(directory):
            continue
        try:
            for fn in os.listdir(directory):
                path = os.path.join(directory, fn)
                key = os.path.normcase(os.path.abspath(path))
                if key in seen or not os.path.isfile(path):
                    continue
                seen.add(key)
                if not _path_matches_entity_runtime_artifact(
                    path=path,
                    entity_safe=entity_safe,
                    entity_uri=entity_uri,
                    allow_iteration_suffix=False,
                ):
                    continue
                try:
                    os.remove(path)
                    deleted += 1
                except Exception as e:
                    logger.warning(
                        f"    ⚠️  Failed to remove canonical entity state {path}: {e}"
                    )
        except Exception as e:
            logger.warning(
                f"    ⚠️  Failed to scan canonical entity state in {directory} for {entity_label}: {e}"
            )

    if deleted:
        logger.info(
            f"    🧼 Reset {deleted} canonical state file(s) for {entity_label}"
        )
    return deleted


def _strip_code_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped, count=1)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    return stripped.strip()


def _merge_section_hint_dicts(base: dict, update: dict) -> dict:
    merged = dict(base or {})
    for key, value in (update or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            child = dict(merged.get(key) or {})
            child.update(value)
            merged[key] = child
        else:
            merged[key] = value
    return merged


def _hint_text_fragments(payload: Any) -> list[str]:
    fragments: list[str] = []
    if isinstance(payload, dict):
        string_keys = [key for key in payload.keys() if isinstance(key, str)]
        if any("BEGIN_ENTITY" in key or "END_ENTITY" in key for key in string_keys):
            fragments.append("\n".join(string_keys))
        for key, value in payload.items():
            if isinstance(key, str) and ("BEGIN_ENTITY" in key or "\n" in key):
                fragments.append(key)
            fragments.extend(_hint_text_fragments(value))
    elif isinstance(payload, list):
        for item in payload:
            fragments.extend(_hint_text_fragments(item))
    elif isinstance(payload, str) and ("BEGIN_ENTITY" in payload or "\n" in payload):
        fragments.append(payload)
    return fragments


def _load_entity_block_hints(payload: Any) -> dict:
    """Parse generic BEGIN_ENTITY hint blocks into class-section property hints."""
    out: dict[str, dict[str, str]] = {}
    fragments = _hint_text_fragments(payload)
    if isinstance(payload, str):
        fragments.append(payload)

    for fragment in fragments:
        current_section: str | None = None
        for raw_line in str(fragment or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            begin_match = re.match(
                r"^BEGIN_ENTITY\s+\S+\s+rdf:type\s+([A-Za-z_][A-Za-z0-9_:\-.]*)\s*$",
                line,
                flags=re.IGNORECASE,
            )
            if begin_match:
                current_section = _local_name(begin_match.group(1))
                if ":" in current_section and "://" not in current_section:
                    current_section = current_section.rsplit(":", 1)[-1]
                out.setdefault(current_section, {})
                continue
            if line.upper().startswith("END_ENTITY"):
                current_section = None
                continue
            if not current_section:
                continue
            coerced = _coerce_hint_kv_line(line)
            if isinstance(coerced, dict):
                out[current_section].update(coerced)
    return out


def _load_structured_hints(hints_text: str) -> dict:
    text = _strip_code_fences(hints_text)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return _merge_section_hint_dicts(parsed, _load_entity_block_hints(parsed))
        return {}
    except Exception:
        pass
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(text)
        if isinstance(parsed, dict):
            return _merge_section_hint_dicts(parsed, _load_entity_block_hints(parsed))
    except Exception:
        pass
    return _merge_section_hint_dicts(
        _load_plain_section_hints(text), _load_entity_block_hints(text)
    )


def _load_plain_section_hints(hints_text: str) -> dict:
    """Parse simple extraction responses with section headers and key/value hints."""
    skip_sections = {"links", "warnings"}
    skip_keys = {
        "subject_iri",
        "iri",
        "case_iri",
        "rdf_type",
        "rdf:type",
        "type",
        "fields",
        "warning",
    }

    out: dict[str, dict[str, str]] = {}
    current: str | None = None

    def normalize_section(raw: str, *, allow_bare: bool) -> str | None:
        section = str(raw or "").strip().strip(":").strip()
        if not section:
            return None
        key = re.sub(r"[^a-z0-9_]+", "", section.lower())
        if key in skip_sections:
            return None
        if allow_bare:
            if "=" in section or ":" in section:
                return None
            if key in skip_keys:
                return None
            if len(section) > 80:
                return None
            if not re.match(r"^[A-Za-z][A-Za-z0-9_\-\s]*$", section):
                return None
            tokens = [tok for tok in re.split(r"[\s_\-]+", section) if tok]
            if not tokens:
                return None
            if not (section.isupper() or all(tok[:1].isupper() for tok in tokens)):
                return None
        return section

    for raw_line in str(hints_text or "").splitlines():
        line = raw_line.strip()
        if not line or line in {"---", "```"}:
            continue
        section_match = re.match(r"^SECTION\s*:\s*(.+?)\s*$", line, flags=re.IGNORECASE)
        if section_match:
            current = normalize_section(section_match.group(1), allow_bare=False)
            if current:
                out.setdefault(current, {})
            continue
        bare_section = normalize_section(line, allow_bare=True)
        if bare_section:
            current = bare_section
            out.setdefault(current, {})
            continue
        if current is None:
            continue
        cleaned = line.lstrip("-").strip()
        if not cleaned:
            continue
        if cleaned.lower().startswith("warning:"):
            continue
        kv_match = re.match(
            r"^([A-Za-z_][A-Za-z0-9_:\-.]*)\s*(?:=|:)\s*(.+?)\s*$", cleaned
        )
        if not kv_match:
            continue
        key = kv_match.group(1).strip()
        value = kv_match.group(2).strip().strip('"')
        key_local = key.rsplit(":", 1)[-1] if ":" in key and "://" not in key else key
        if key.lower() in skip_keys or key_local.lower() in skip_keys:
            continue
        if key_local.startswith("has") or key_local in {
            "created",
            "status",
            "message",
            "code",
            "warning",
        }:
            continue
        if value and value.lower() not in {"not provided", "unknown", "n/a", "na", "-"}:
            out.setdefault(current, {})[key] = value
    return out


def _get_ordered_member_hint_contracts(main_entity_policy: dict) -> list[dict]:
    publish_policy = (main_entity_policy or {}).get("publish", {}) or {}
    hint_reconciliation = publish_policy.get("hint_reconciliation", {}) or {}
    contracts = hint_reconciliation.get("ordered_member_hint_contracts", []) or []
    return [contract for contract in contracts if isinstance(contract, dict)]


def _extract_ordered_member_expectations(
    hints_text: str, contract: dict
) -> list[dict[str, Any]]:
    payload = _load_structured_hints(hints_text)
    section_name = str((contract or {}).get("section_name") or "").strip()
    members = (
        payload.get(section_name)
        if isinstance(payload, dict) and section_name
        else None
    )
    if not isinstance(members, list):
        return []
    order_keys = (contract or {}).get("order_keys") or ["hasOrder"]
    if not isinstance(order_keys, list):
        order_keys = ["hasOrder"]
    type_keys = (contract or {}).get("type_keys") or ["rdf:type", "type"]
    if not isinstance(type_keys, list):
        type_keys = ["rdf:type", "type"]
    type_namespace_iri = str((contract or {}).get("type_namespace_iri") or "").strip()
    expectations: list[dict[str, Any]] = []
    for item in members:
        if not isinstance(item, dict):
            continue
        raw_type_values: list[str] = []
        for key in type_keys:
            raw_value = item.get(str(key))
            if isinstance(raw_value, list):
                raw_type_values = [
                    str(value).strip()
                    for value in raw_value
                    if str(value or "").strip()
                ]
            elif str(raw_value or "").strip():
                raw_type_values = [str(raw_value).strip()]
            if raw_type_values:
                break
        raw_order = None
        for key in order_keys:
            if str(key) in item:
                raw_order = item.get(str(key))
                break
        if not raw_type_values or raw_order is None:
            continue
        try:
            order = int(raw_order)
        except Exception:
            continue
        raw_type = ""
        for candidate in raw_type_values:
            if _local_name(candidate).lower() not in {"synthesisstep", "orderedmember"}:
                raw_type = candidate
                break
        raw_type = raw_type or raw_type_values[0]
        if raw_type.startswith(("http://", "https://")):
            type_iri = raw_type
        elif type_namespace_iri:
            type_iri = f"{type_namespace_iri}{_local_name(raw_type.rsplit(':', 1)[-1])}"
        else:
            type_iri = ""
        if not type_iri:
            continue
        expectations.append(
            {
                "order": order,
                "type_iri": type_iri,
                "label": item.get("rdfs:label") or item.get("label") or "",
            }
        )
    return expectations


def _validate_ordered_members_against_hints(
    *,
    ttl_path: str,
    hints_content: str,
    main_entity_policy: dict,
) -> tuple[bool, list[str]]:
    contracts = _get_ordered_member_hint_contracts(main_entity_policy)
    if not contracts:
        return True, []
    expectations: list[dict[str, Any]] = []
    order_predicate_iris: dict[str, str] = {}
    for contract in contracts:
        order_predicate_iri = str(
            (contract or {}).get("order_predicate_iri") or ""
        ).strip()
        if not order_predicate_iri:
            continue
        extracted = _extract_ordered_member_expectations(hints_content, contract)
        for item in extracted:
            order_predicate_iris[str(item.get("type_iri") or "")] = order_predicate_iri
        expectations.extend(extracted)
    if not expectations:
        return True, []
    try:
        g = Graph()
        g.parse(ttl_path, format="turtle")
    except Exception as e:
        return False, [f"Failed to parse TTL for ordered-step validation: {e}"]

    errors: list[str] = []
    for expected in expectations:
        order = expected["order"]
        type_iri = str(expected.get("type_iri") or "").strip()
        order_predicate_iri = order_predicate_iris.get(type_iri, "")
        if not type_iri or not order_predicate_iri:
            continue
        matches: list[URIRef] = []
        for node in g.subjects(RDF.type, URIRef(type_iri)):
            if isinstance(node, URIRef) and _step_has_order(
                g, node, order, order_predicate_iri=order_predicate_iri
            ):
                matches.append(node)
        if not matches:
            errors.append(
                f"Missing hinted ordered member: order={order}, type={type_iri}. "
                "Create this exact ordered member and link it to the scoped top entity."
            )
        elif len(matches) > 1:
            labels = [_first_label(g, node) or str(node) for node in matches]
            errors.append(
                f"Duplicate hinted ordered member: order={order}, type={type_iri}, matches={labels}. "
                "Keep exactly one individual for this semantic step."
            )
    return len(errors) == 0, errors


def _merge_hint_payloads(base: dict, update: dict) -> dict:
    merged = dict(base or {})
    for key, value in (update or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_hint_payloads(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parse_generic_parameter_hints(hints_text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current and current.get("parameters"):
            items.append(current)
        current = None

    for raw_line in str(hints_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.upper() == "STEP":
            flush()
            current = {"parameters": []}
            continue
        if current is None:
            continue
        if line.lower().startswith("step_ref:"):
            current["step_ref"] = line.split(":", 1)[1].strip()
        elif line.lower().startswith("rdf_type:"):
            raw_type = line.split(":", 1)[1].strip()
            current["type_local"] = (
                raw_type.rsplit(":", 1)[-1]
                if ":" in raw_type and "://" not in raw_type
                else _local_name(raw_type)
            )
        elif order_match := re.match(
            r".*\bhasOrder\s*:\s*(\d+)\b", line, flags=re.IGNORECASE
        ):
            current["order"] = int(order_match.group(1))
        elif param_match := re.match(
            r".*\bhasParameter\s*:\s*([A-Za-z][A-Za-z0-9_\- ]*)\s*=\s*"
            r"([-+]?\d+(?:\.\d+)?)\s*([^\s,;]+)",
            line,
            flags=re.IGNORECASE,
        ):
            current.setdefault("parameters", []).append(
                {
                    "name": param_match.group(1).strip(),
                    "value": float(param_match.group(2)),
                    "unit": param_match.group(3).strip(),
                }
            )
    flush()
    return items


def _load_generated_quantity_property_locals(
    *, ontology_name: str, class_local: str
) -> list[str]:
    root = (
        os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "").strip()
        or "ai_generated_contents_candidate"
    )
    main_path = Path(root) / "scripts" / ontology_name / "main.py"
    try:
        tree = ast.parse(main_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    for node in tree.body:
        if (
            not isinstance(node, ast.FunctionDef)
            or node.name != f"create_{class_local}"
        ):
            continue
        arg_names = {arg.arg for arg in node.args.args}
        return sorted(
            name[: -len("_value")]
            for name in arg_names
            if name.endswith("_value") and f"{name[: -len('_value')]}_unit" in arg_names
        )
    return []


def _choose_quantity_property_local(param_name: str, candidates: list[str]) -> str:
    wanted = re.sub(r"[^a-z0-9]+", "", str(param_name or "").lower())
    best: tuple[int, str] = (0, "")
    for prop in candidates:
        prop_norm = re.sub(r"[^a-z0-9]+", "", prop.lower())
        score = 0
        if wanted and wanted in prop_norm:
            score += 10
        if wanted and prop_norm.endswith(wanted):
            score += 3
        if wanted == "temperature" and "target" in prop_norm:
            score += 2
        if wanted == "temperature" and "rate" in prop_norm:
            score -= 4
        if score > best[0]:
            best = (score, prop)
    return best[1]


def _normalize_om2_unit_local(raw_unit: str) -> str:
    unit = str(raw_unit or "").strip().lower()
    aliases = {
        "h": "hour",
        "hr": "hour",
        "hrs": "hour",
        "hours": "hour",
        "min": "minute",
        "mins": "minute",
        "minutes": "minute",
        "s": "second",
        "sec": "second",
        "seconds": "second",
        "c": "degreeCelsius",
        "°c": "degreeCelsius",
        "degc": "degreeCelsius",
        "degreecelsius": "degreeCelsius",
        "k": "kelvin",
    }
    return aliases.get(unit, str(raw_unit or "").strip())


def _quantity_class_for(prop_local: str, unit_local: str) -> URIRef:
    key = f"{prop_local} {unit_local}".lower()
    if "temperature" in key or "celsius" in key or "kelvin" in key:
        return OM2.Temperature
    if "duration" in key or unit_local in {"hour", "minute", "second", "day"}:
        return OM2.Duration
    return OM2.Quantity


def _step_has_order(
    g: Graph, node: URIRef, order: Any, order_predicate_iri: str = ""
) -> bool:
    expected_pred = URIRef(order_predicate_iri) if order_predicate_iri else None
    for pred, obj in g.predicate_objects(node):
        if expected_pred is not None and pred != expected_pred:
            continue
        if _local_name(pred) != "hasOrder":
            continue
        try:
            return int(obj) == int(order)
        except Exception:
            return str(obj) == str(order)
    return False


def _find_step_node_for_hint(g: Graph, hint: dict[str, Any]) -> Optional[URIRef]:
    step_ref = str(hint.get("step_ref") or "").strip()
    type_local = str(hint.get("type_local") or "").strip()
    order = hint.get("order")
    candidates: list[URIRef] = []
    for node in (
        g.subjects(RDFS.label, Literal(step_ref))
        if step_ref
        else g.subjects(None, None)
    ):
        if not isinstance(node, URIRef):
            continue
        if type_local and not any(
            _local_name(t) == type_local for t in g.objects(node, RDF.type)
        ):
            continue
        if order is not None and not _step_has_order(g, node, order):
            continue
        candidates.append(node)
    return sorted(set(candidates), key=str)[0] if candidates else None


def _repair_generic_parameter_quantity_hints(
    *,
    ttl_path: str,
    raw_hints: list[str],
    ontology_name: str,
    property_namespace_iri: str,
) -> tuple[bool, list[str]]:
    try:
        g = Graph()
        g.parse(ttl_path, format="turtle")
    except Exception as e:
        return False, [
            f"Failed to parse published TTL for generic parameter repair: {e}"
        ]

    changed = False
    messages: list[str] = []
    for text in raw_hints:
        for hint in _parse_generic_parameter_hints(text):
            step = _find_step_node_for_hint(g, hint)
            if step is None:
                continue
            candidates = _load_generated_quantity_property_locals(
                ontology_name=ontology_name,
                class_local=str(hint.get("type_local") or "").strip(),
            )
            for param in hint.get("parameters") or []:
                prop_local = _choose_quantity_property_local(
                    str(param.get("name") or ""), candidates
                )
                if not prop_local:
                    continue
                pred = URIRef(f"{property_namespace_iri}{prop_local}")
                if any(True for _ in g.objects(step, pred)):
                    continue
                unit_local = _normalize_om2_unit_local(str(param.get("unit") or ""))
                value = float(param.get("value"))
                digest = hashlib.sha1(
                    f"{step}|{pred}|{value}|{unit_local}".encode("utf-8")
                ).hexdigest()
                q = URIRef(
                    f"https://www.theworldavatar.com/kg/instance/OM2Quantity/{digest}"
                )
                g.add((q, RDF.type, _quantity_class_for(prop_local, unit_local)))
                g.add(
                    (
                        q,
                        RDFS.label,
                        Literal(
                            f"{_first_label(g, step) or _local_name(step)} {prop_local}"
                        ),
                    )
                )
                g.add((q, OM2.hasNumericalValue, Literal(value)))
                g.add((q, OM2.hasUnit, URIRef(f"{str(OM2)}{unit_local}")))
                g.add((step, pred, q))
                changed = True
                messages.append(
                    f"Materialized generic parameter {param.get('name')} as {prop_local} on {_first_label(g, step) or step}"
                )

    if changed:
        try:
            g.serialize(destination=ttl_path, format="turtle")
        except Exception as e:
            return False, messages + [
                f"Failed to write generic parameter repaired TTL: {e}"
            ]
    return True, messages


def _prune_unhinted_orphan_required_targets(
    *,
    ttl_path: str,
    raw_hints: list[str],
    main_entity_policy: dict,
) -> tuple[bool, list[str]]:
    """Remove unlinked required-target placeholders that are not mentioned in hints."""
    try:
        g = Graph()
        g.parse(ttl_path, format="turtle")
    except Exception as e:
        return False, [f"Failed to parse published TTL for orphan pruning: {e}"]

    raw_text = "\n".join(str(x or "") for x in raw_hints)
    shell_validation = (main_entity_policy or {}).get("shell_validation", {}) or {}
    required_links = shell_validation.get("required_links", []) or []
    messages: list[str] = []
    changed = False

    for spec in required_links:
        pred_iri = str((spec or {}).get("predicate_iri") or "").strip()
        target_class_iri = str((spec or {}).get("target_class_iri") or "").strip()
        if not pred_iri or not target_class_iri:
            continue
        pred = URIRef(pred_iri)
        target_cls = URIRef(target_class_iri)
        linked = {
            o for _, _, o in g.triples((None, pred, None)) if isinstance(o, URIRef)
        }
        for node in list(g.subjects(RDF.type, target_cls)):
            if not isinstance(node, URIRef) or node in linked:
                continue
            label = _first_label(g, node)
            if label and label in raw_text:
                continue
            for triple in list(g.triples((node, None, None))):
                g.remove(triple)
            for triple in list(g.triples((None, None, node))):
                g.remove(triple)
            changed = True
            messages.append(f"Pruned unhinted orphan required target {label or node}")

    if changed:
        try:
            g.serialize(destination=ttl_path, format="turtle")
        except Exception as e:
            return False, messages + [f"Failed to write orphan-pruned TTL: {e}"]
    return True, messages


def _choose_preferred_typed_target(
    g: Graph, typed_targets: List[URIRef]
) -> Optional[URIRef]:
    if not typed_targets:
        return None

    def _score(node: URIRef) -> tuple[int, str]:
        outgoing = sum(1 for _ in g.triples((node, None, None)))
        incoming = sum(1 for _ in g.triples((None, None, node)))
        return (outgoing + incoming, str(node))

    return sorted(typed_targets, key=_score, reverse=True)[0]


def _remap_graph_nodes(g: Graph, remap: Dict[URIRef, URIRef]) -> Graph:
    if not remap:
        return g
    rewritten = Graph()
    for prefix, ns in g.namespaces():
        rewritten.bind(prefix, ns)
    for s, p, o in g:
        new_s = remap.get(s, s) if isinstance(s, URIRef) else s
        new_o = remap.get(o, o) if isinstance(o, URIRef) else o
        rewritten.add((new_s, p, new_o))
    return rewritten


def _repair_published_entity_ttl_from_hints(
    *,
    ttl_path: str,
    entity_uri: str,
    entity_label: str,
    aggregated_hints: dict,
    ontology_name: str,
    main_entity_policy: dict,
) -> tuple[bool, list[str]]:
    """
    Reconcile the published entity TTL against the union of structured hints across iterations.

    This is intentionally conservative:
    - collapse duplicate singleton targets linked to the top entity
    - materialize explicit hinted leaf properties on the chosen linked node
    - drop optional singleton links when no iteration hinted that section at all
    """
    if not ttl_path or not os.path.exists(ttl_path):
        return False, [f"Published TTL not found for hint reconciliation: {ttl_path}"]

    try:
        g = Graph()
        g.parse(ttl_path, format="turtle")
    except Exception as e:
        return False, [f"Failed to parse published TTL for hint reconciliation: {e}"]

    shell_validation = (main_entity_policy or {}).get("shell_validation", {}) or {}
    top_class_iri = str(shell_validation.get("top_entity_class_iri") or "").strip()
    label_key_suffixes = shell_validation.get("label_key_suffixes_to_strip") or []
    if not isinstance(label_key_suffixes, list):
        label_key_suffixes = []
    resolved_entity_uri = _resolve_expected_top_entity_uri(
        g,
        top_class_iri=top_class_iri,
        entity_uri=entity_uri,
        entity_label=entity_label,
        label_key_suffixes_to_strip=label_key_suffixes,
    )
    top_entity = URIRef(resolved_entity_uri) if resolved_entity_uri else None
    if top_entity is None:
        return False, ["Missing top-level entity IRI for hint reconciliation"]

    messages: list[str] = []
    exclusive_property_groups = _get_hint_exclusive_property_groups(main_entity_policy)
    for spec in _get_hint_reconciliation_specs(main_entity_policy):
        section_name = str(spec.get("section_name") or "").strip()
        pred_iri = str(spec.get("predicate_iri") or "").strip()
        target_class_iri = str(spec.get("target_class_iri") or "").strip()
        property_namespace_iri = str(spec.get("property_namespace_iri") or "").strip()
        if not (
            section_name and pred_iri and target_class_iri and property_namespace_iri
        ):
            continue
        pred = URIRef(pred_iri)
        target_cls = URIRef(target_class_iri)
        optional = bool(spec.get("optional"))
        hinted_section = _extract_hinted_section_payload(aggregated_hints, spec)

        current_targets = [
            o
            for o in g.objects(top_entity, pred)
            if isinstance(o, URIRef) and (o, RDF.type, target_cls) in g
        ]

        is_ordered_member = bool(spec.get("ordered_member"))
        if len(current_targets) > 1 and not is_ordered_member:
            canonical = _choose_preferred_typed_target(g, current_targets)
            if canonical is not None:
                remap = {
                    node: canonical for node in current_targets if node != canonical
                }
                if remap:
                    g = _remap_graph_nodes(g, remap)
                    messages.append(
                        f"Collapsed {len(current_targets)} linked {section_name} nodes into singleton {canonical}"
                    )
                    current_targets = [canonical]

        if hinted_section is None and optional:
            if current_targets:
                for node in current_targets:
                    g.remove((top_entity, pred, node))
                messages.append(
                    f"Removed stale optional link(s) for {section_name} because no iteration hints contained that section"
                )
            continue

        if not isinstance(hinted_section, dict):
            continue

        if is_ordered_member:
            preferred_targets = _preferred_ordered_member_targets(
                g, target_cls=target_cls
            )
            if preferred_targets:
                existing_links = [
                    o
                    for o in g.objects(top_entity, pred)
                    if isinstance(o, URIRef) and (o, RDF.type, target_cls) in g
                ]
                desired_set = set(preferred_targets)
                for node in existing_links:
                    if node not in desired_set:
                        g.remove((top_entity, pred, node))
                for node in preferred_targets:
                    if (top_entity, pred, node) not in g:
                        g.add((top_entity, pred, node))
                current_targets = preferred_targets
                messages.append(
                    f"Reconciled ordered members for {section_name}: attached {len(preferred_targets)} concrete node(s) and dropped placeholder/duplicate links"
                )

        if not current_targets:
            typed_targets = sorted(
                {s for s in g.subjects(RDF.type, target_cls) if isinstance(s, URIRef)},
                key=str,
            )
            canonical = _choose_preferred_typed_target(g, typed_targets)
            if canonical is not None:
                g.add((top_entity, pred, canonical))
                current_targets = [canonical]
                messages.append(
                    f"Reattached hinted {section_name} singleton {canonical} to top entity"
                )

        if not current_targets and isinstance(hinted_section, dict):
            canonical = _materialize_placeholder_target(
                g,
                target_cls=target_cls,
                label=section_name,
            )
            g.add((top_entity, pred, canonical))
            current_targets = [canonical]
            messages.append(
                f"Materialized missing hinted {section_name} target and attached it to the top entity"
            )

        if not current_targets and is_ordered_member:
            placeholder_label = (
                f"{_first_label(g, top_entity) or 'Hinted'} ordered member"
            )
            canonical = _materialize_placeholder_target(
                g,
                target_cls=target_cls,
                label=placeholder_label,
            )
            g.add((top_entity, pred, canonical))
            current_targets = [canonical]
            messages.append(
                f"Materialized placeholder {section_name} from hints and attached it to the top entity"
            )

        if not current_targets:
            continue

        target_node = current_targets[0]
        hinted_property_iris = {
            _resolve_hint_property_iri(
                property_namespace_iri=property_namespace_iri,
                prop_name=prop_name,
            )
            for prop_name, prop_value in hinted_section.items()
            if not isinstance(prop_value, (dict, list))
            and prop_value is not None
            and not str(prop_name).strip().endswith("_label")
        }
        if bool(spec.get("prune_unhinted_scalar_properties")):
            namespace = str(property_namespace_iri or "")
            removed = 0
            for pred_existing, obj_existing in list(g.predicate_objects(target_node)):
                if pred_existing in {RDF.type, RDFS.label}:
                    continue
                if namespace and not str(pred_existing).startswith(namespace):
                    continue
                if isinstance(obj_existing, URIRef):
                    continue
                if pred_existing in hinted_property_iris:
                    continue
                g.remove((target_node, pred_existing, obj_existing))
                removed += 1
            if removed:
                messages.append(
                    f"Pruned {removed} unhinted scalar propert{'y' if removed == 1 else 'ies'} from {section_name}"
                )
        for group in exclusive_property_groups:
            if str(group.get("target_class_iri") or "").strip() != target_class_iri:
                continue
            group_props = {
                URIRef(prop_iri)
                for prop_iri in group.get("property_iris", [])
                if str(prop_iri or "").strip()
            }
            if not group_props:
                continue
            hinted_props = hinted_property_iris & group_props
            if not hinted_props:
                continue
            for sibling_prop in sorted(group_props - hinted_props, key=str):
                removed = 0
                for old in list(g.objects(target_node, sibling_prop)):
                    g.remove((target_node, sibling_prop, old))
                    removed += 1
                if removed:
                    messages.append(
                        f"Removed non-hinted mutually exclusive property {_local_name(str(sibling_prop))} from {target_node}"
                    )
        for prop_name, prop_value in hinted_section.items():
            if isinstance(prop_value, (dict, list)) or prop_value is None:
                continue
            # Object-property hint parameters use a `_label` suffix so the
            # generated constructor can resolve/create the target and emit the
            # real object triple. They are never ontology datatype properties.
            if str(prop_name).strip().endswith("_label"):
                continue
            # Hint JSON uses `label` for rdfs:label; never mint a domain `*:label` datatype.
            if str(prop_name).strip() in {"label", "rdfs:label"}:
                label_text = str(prop_value).strip()
                if label_text and (target_node, RDFS.label, Literal(label_text)) not in g:
                    for old in list(g.objects(target_node, RDFS.label)):
                        if str(old).strip() != label_text:
                            g.remove((target_node, RDFS.label, old))
                    g.add((target_node, RDFS.label, Literal(label_text)))
                continue
            prop = _resolve_hint_property_iri(
                property_namespace_iri=property_namespace_iri,
                prop_name=prop_name,
            )
            desired_literal = Literal(str(prop_value))
            for old in list(g.objects(target_node, prop)):
                if old != desired_literal:
                    g.remove((target_node, prop, old))
            if (target_node, prop, desired_literal) not in g:
                g.add((target_node, prop, desired_literal))
                messages.append(
                    f"Materialized hinted property {section_name}.{prop_name} on {target_node}"
                )

        if (ontology_name or "").strip() == "medical":
            _normalize_medical_composite_graph(g)

    try:
        g.serialize(destination=ttl_path, format="turtle")
    except Exception as e:
        return False, messages + [f"Failed to write hint-reconciled TTL: {e}"]

    return True, messages


def _find_hints_file(*, mcp_run_dir: str, iter_num: int, entity_safe: str) -> str:
    """
    Return the best matching hints file path.

    Handles legacy filename sanitization differences by scanning `mcp_run_dir` for
    `iter{n}_hints_*.txt` and comparing normalized safe names.
    """
    expected = os.path.join(mcp_run_dir, f"iter{iter_num}_hints_{entity_safe}.txt")
    if os.path.exists(expected):
        return expected

    prefix = f"iter{iter_num}_hints_"
    try:
        for fn in os.listdir(mcp_run_dir):
            if not (fn.startswith(prefix) and fn.lower().endswith(".txt")):
                continue
            cand_safe = fn[len(prefix) : -4]
            if _safe_name(cand_safe) == entity_safe:
                return os.path.join(mcp_run_dir, fn)
    except Exception:
        pass

    return expected


def _find_enrichment_patch_files(
    *, mcp_run_dir: str, iter_num: int, entity_safe: str
) -> list[str]:
    """Return enrichment patch files that refine a base iteration's hints."""
    prefix = f"iter{iter_num}_"
    suffix = f"_{entity_safe}.txt"
    out: list[str] = []
    try:
        for fn in os.listdir(mcp_run_dir):
            if not (fn.startswith(prefix) and fn.endswith(suffix)):
                continue
            if "_patch_" not in fn:
                continue
            out.append(os.path.join(mcp_run_dir, fn))
    except Exception:
        return []
    return sorted(out)


def _merge_entity_ttl_candidates(
    *,
    candidate_paths: List[str],
    intermediate_ttl: str,
    entity_label: str,
) -> bool:
    """
    Merge all valid candidate TTL shards for an entity into one intermediate TTL.

    Some MCP server runs emit multiple partial TTL files for the same case under
    different filename conventions. We union all matching shards so later publish
    steps do not silently drop child entities.
    """
    existing: list[str] = []
    seen: set[str] = set()
    for path in candidate_paths:
        if not path or not os.path.exists(path):
            continue
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        existing.append(path)

    if not existing:
        return False

    if len(existing) == 1:
        shutil.copy2(existing[0], intermediate_ttl)
        logger.info(f"    ✅ Saved intermediate TTL: {os.path.basename(existing[0])}")
        return True

    merged = Graph()
    parsed: list[str] = []
    for src in sorted(existing, key=lambda p: os.path.getmtime(p), reverse=True):
        try:
            merged.parse(src, format="turtle")
            parsed.append(src)
        except Exception as e:
            logger.warning(
                f"    ⚠️  Failed to parse TTL shard for {entity_label}: {os.path.basename(src)} ({e})"
            )

    if not parsed:
        return False

    try:
        merged.serialize(destination=intermediate_ttl, format="turtle")
    except Exception as e:
        logger.warning(f"    ⚠️  Failed to merge TTL shards for {entity_label}: {e}")
        return False

    logger.info(
        "    ✅ Saved merged intermediate TTL from %d shard(s): %s",
        len(parsed),
        ", ".join(os.path.basename(p) for p in parsed),
    )
    return True


def _try_copy_entity_ttl_to_intermediate(
    *,
    doi_folder: str,
    entity_label: str,
    entity_safe: str,
    entity_uri: str = "",
    intermediate_ttl: str,
) -> bool:
    """
    Copy the best-available per-entity TTL into `intermediate_ttl`.

    Canonical persistence locations used across MCP servers in this repo:
      - data/<hash>/memory/<entity_file>.ttl
      - data/<hash>/exports/<entity_file>_<timestamp>.ttl

    Some generated servers persist entity files under the *raw label* rather than
    the pipeline's sanitized `entity_safe` name. We handle this by scanning
    memory/exports and matching via `_safe_name(...)`.
    """
    os.makedirs(os.path.dirname(intermediate_ttl), exist_ok=True)
    candidates: list[str] = []

    memory_ttl = os.path.join(doi_folder, "memory", f"{entity_safe}.ttl")
    if os.path.exists(memory_ttl):
        candidates.append(memory_ttl)

    # Fallback: scan memory/ for label-derived filenames and match via normalization
    mem_dir = os.path.join(doi_folder, "memory")
    try:
        if os.path.isdir(mem_dir):
            for fn in os.listdir(mem_dir):
                if not fn.lower().endswith(".ttl"):
                    continue
                if fn.lower() == "top.ttl":
                    continue
                stem = fn[:-4]
                if _safe_name(stem) == entity_safe:
                    candidates.append(os.path.join(mem_dir, fn))
    except Exception as e:
        logger.warning(f"    ⚠️  Error scanning memory TTLs for {entity_label}: {e}")

    exports_dir = os.path.join(doi_folder, "exports")
    try:
        if os.path.isdir(exports_dir):
            export_candidates = [
                os.path.join(exports_dir, f)
                for f in os.listdir(exports_dir)
                if f.lower().startswith(entity_safe.lower() + "_")
                and f.lower().endswith(".ttl")
            ]
            if export_candidates:
                candidates.extend(export_candidates)

            # Fallback: scan exports/ and match label-derived prefixes
            def _strip_ts(stem: str) -> str:
                parts = (stem or "").split("_")
                # common pattern: <name>_YYYYMMDD_HHMMSS
                if (
                    len(parts) >= 3
                    and parts[-2].isdigit()
                    and len(parts[-2]) == 8
                    and parts[-1].isdigit()
                    and len(parts[-1]) == 6
                ):
                    return "_".join(parts[:-2])
                # best-effort: strip one numeric suffix
                if len(parts) >= 2 and parts[-1].isdigit():
                    return "_".join(parts[:-1])
                return stem

            for fn in os.listdir(exports_dir):
                if not fn.lower().endswith(".ttl"):
                    continue
                stem = fn[:-4]
                base = _strip_ts(stem)
                if _safe_name(base) == entity_safe:
                    candidates.append(os.path.join(exports_dir, fn))

            # Last-resort fallback: the agent may have used a shorter entity name.
            # Scan every non-top TTL and pick the one whose content contains the expected entity IRI.
            if entity_uri:
                iri_token = f"<{entity_uri}>"
                candidates_by_iri: list[str] = []
                for fn in os.listdir(exports_dir):
                    if not fn.lower().endswith(".ttl") or fn.lower() == "top.ttl":
                        continue
                    try:
                        content = open(
                            os.path.join(exports_dir, fn), encoding="utf-8"
                        ).read()
                        if iri_token in content:
                            candidates_by_iri.append(os.path.join(exports_dir, fn))
                    except Exception:
                        continue
                if candidates_by_iri:
                    candidates.extend(candidates_by_iri)

    except Exception as e:
        logger.warning(f"    ⚠️  Error scanning exports TTLs for {entity_label}: {e}")

    if candidates:
        unique_candidates = list(dict.fromkeys(candidates))

        def _contains_entity_iri(path: str) -> bool:
            if not entity_uri:
                return False
            iri_token = f"<{entity_uri}>"
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return iri_token in fh.read()
            except Exception:
                return False

        def _score_candidate(path: str) -> tuple[int, int, float, str]:
            normalized_path = os.path.normcase(os.path.abspath(path))
            memory_dir = os.path.normcase(
                os.path.abspath(os.path.join(doi_folder, "memory"))
            )
            in_memory_dir = int(normalized_path.startswith(memory_dir))
            matches_entity_iri = int(_contains_entity_iri(path))
            try:
                mtime = os.path.getmtime(path)
            except Exception:
                mtime = 0.0
            return (in_memory_dir, matches_entity_iri, mtime, normalized_path)

        best_candidate = sorted(unique_candidates, key=_score_candidate, reverse=True)[
            0
        ]
        try:
            shutil.copy2(best_candidate, intermediate_ttl)
            logger.info(
                "    ✅ Saved intermediate TTL from best candidate: %s",
                os.path.relpath(best_candidate, doi_folder),
            )
            return True
        except Exception as e:
            logger.warning(
                f"    ⚠️  Failed to copy best entity TTL for {entity_label}: {e}"
            )

    # Backward-compat fallback (rarely produced by the created MCP server)
    output_ttl = os.path.join(doi_folder, "output.ttl")
    if os.path.exists(output_ttl):
        shutil.copy2(output_ttl, intermediate_ttl)
        logger.info(f"    ✅ Saved intermediate TTL from output.ttl")
        return True

    logger.warning(
        f"    ⚠️  No entity TTL found for {entity_label} (safe={entity_safe})"
    )
    return False


def load_prompt(prompt_path: str, project_root: str = ".") -> str:
    """Load prompt from file."""
    full_path = resolve_generated_file(prompt_path, project_root=project_root)
    if not os.path.exists(full_path):
        logger.error(f"Prompt file not found: {full_path}")
        return ""

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to load prompt from {full_path}: {e}")
        return ""


async def run_kg_building_agent(
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    hints_content: str,
    kg_prompt: str,
    iter_num: int,
    mcp_tools: List[str],
    mcp_set_name: str,
    data_dir: str = "data",
    main_entity_policy: Optional[dict] = None,
    agent_model: str = "gpt-4o",
) -> str:
    """
    Run KG building agent for a single entity.

    Args:
        doi_hash: DOI hash for the paper
        entity_label: Entity label
        entity_uri: Entity URI
        hints_content: Extraction hints content
        kg_prompt: KG building prompt template
        iter_num: Iteration number
        mcp_tools: List of MCP tools to use
        mcp_set_name: MCP set name
        data_dir: Data directory

    Returns:
        Agent response content
    """
    safe = _safe_name(entity_label)
    doi_folder = os.path.join(data_dir, doi_hash)

    # Replace placeholders in prompt
    prompt = kg_prompt.replace("{doi}", doi_hash)
    # Legacy generated prompts used {hash}; keep both substitutions.
    prompt = prompt.replace("{hash}", doi_hash)
    prompt = prompt.replace("{entity_label}", entity_label)
    prompt = prompt.replace("{entity_uri}", entity_uri)
    structured_hints = (
        "These are extracted hints for this iteration. Treat them as the primary source for KG building.\n"
        "Do not downgrade an explicit canonical field in these hints into a weaker fallback field.\n\n"
        "ExtractedHints:\n<<<\n"
        f"{hints_content}\n"
        ">>>\n"
    )
    prompt = prompt.replace("{paper_content}", structured_hints)
    prompt = _augment_kg_prompt_with_runtime_rules(
        kg_prompt=prompt,
        entity_label=entity_label,
        entity_uri=entity_uri,
        doi_hash=doi_hash,
        main_entity_policy=main_entity_policy or {},
        hints_content=hints_content,
    )

    # Add orphan entity check instruction
    prompt += (
        "\n\n"
        "Before exporting the final TTL/memory, call the tool `check_orphan_entities` to detect any orphan entities. "
        "If any are found, attempt to connect them appropriately to the scoped top entity, ordered members, linked child entities, or parameters. "
        "If you cannot connect some, list their details in your response and proceed with export."
    )

    # Save full prompt
    kg_prompts_dir = os.path.join(doi_folder, "prompts", f"iter{iter_num}_kg_building")
    os.makedirs(kg_prompts_dir, exist_ok=True)
    prompt_file = os.path.join(kg_prompts_dir, f"{safe}.md")
    try:
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(f"# Iteration {iter_num} KG Building Prompt\n\n")
            f.write(f"**Entity**: {entity_label}\n\n")
            f.write(f"**Entity URI**: {entity_uri}\n\n")
            f.write(f"**MCP Tools**: {mcp_tools}\n\n")
            f.write(f"**MCP Set**: {mcp_set_name}\n\n")
            f.write("---\n\n")
            f.write(prompt)
    except Exception as e:
        logger.warning(f"Failed to save prompt to {prompt_file}: {e}")

    # Write global state for MCP server
    write_global_state(doi_hash, safe, entity_uri)

    # Run agent with retry
    max_retries = 3
    retry_prompt = prompt
    for attempt in range(max_retries):
        try:
            logger.info(
                f"    🚀 Running KG building agent for '{entity_label}' (iter {iter_num})"
            )
            BaseAgent = _get_base_agent()
            agent = BaseAgent(
                model_name=agent_model,
                model_config=ModelConfig(temperature=0.1, top_p=0.1),
                remote_model=True,
                mcp_tools=mcp_tools,
                mcp_set_name=mcp_set_name,
            )
            logger.info(f"    Agent execution attempt {attempt + 1}/{max_retries}")
            response, metadata = await agent.run(retry_prompt, recursion_limit=80)
            logger.info(f"    ✅ Agent execution succeeded on attempt {attempt + 1}")

            # CRITICAL: Wait for MCP server operations to complete before proceeding
            # The MCP server is a separate process that may have delayed I/O operations
            logger.info(f"    ⏳ Waiting for MCP server operations to complete...")
            await asyncio.sleep(3)

            # Note: Direct export removed to avoid race condition with global state
            # The agent should call export_memory through MCP tools with explicit parameters
            logger.info(f"    ✅ MCP server operations completed")

            # Save response
            kg_responses_dir = os.path.join(
                doi_folder, "responses", f"iter{iter_num}_kg_building"
            )
            os.makedirs(kg_responses_dir, exist_ok=True)
            response_file = os.path.join(kg_responses_dir, f"{safe}.md")
            try:
                with open(response_file, "w", encoding="utf-8") as f:
                    f.write(f"# Iteration {iter_num} KG Building Response\n\n")
                    f.write(f"**Entity**: {entity_label}\n\n")
                    f.write("---\n\n")
                    f.write(str(response))
            except Exception as e:
                logger.warning(f"Failed to save response to {response_file}: {e}")

            response_text = str(response)
            if _response_claims_persistence(
                response_text
            ) and not _has_entity_persistence_artifact(
                doi_folder=doi_folder,
                entity_safe=safe,
                entity_label=entity_label,
            ):
                response_text = (
                    response_text
                    + "\n\nRECOVERABLE_KG_ERROR: response claimed graph persistence/export, "
                    "but no entity TTL artifact was found in memory or exports."
                )
            if _response_has_recoverable_kg_error(response_text):
                if attempt < max_retries - 1:
                    logger.warning(
                        "    ♻️  Recoverable KG agent response detected for '%s'; retrying with recovery instructions",
                        entity_label,
                    )
                    write_global_state(doi_hash, safe, entity_uri)
                    retry_prompt = _build_kg_recovery_prompt(
                        base_prompt=prompt,
                        entity_label=entity_label,
                        entity_uri=entity_uri,
                    )
                    await asyncio.sleep(3)
                    continue
                logger.error(
                    "    ❌ KG agent kept returning recoverable subject/container errors after %d attempts",
                    max_retries,
                )
                raise RuntimeError(
                    "KG agent returned repeated recoverable persistence/tool-use errors"
                )

            return response_text

        except Exception as e:
            logger.error(
                f"    Agent execution failed on attempt {attempt + 1}/{max_retries}: {e}"
            )
            if attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)
                logger.info(f"    Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    f"    ❌ Agent execution failed after {max_retries} attempts"
                )
                raise RuntimeError(
                    f"Agent execution failed after {max_retries} attempts. Last error: {e}"
                )


def _try_generated_materialize_hints(
    *,
    doi_hash: str,
    data_dir: str,
    ontology_name: str,
    project_root: str,
    entity_label: str,
    entity_safe: str,
    hints_content: str,
    intermediate_ttl: str,
    response_file: str,
) -> bool:
    """Use the generated ontology MCP implementation as the deterministic primary path."""
    if not ontology_name:
        return False
    roots: list[str] = []
    override = os.environ.get("TWA_GENERATED_ARTIFACT_ROOT", "").strip()
    if override:
        roots.append(override)
    roots.extend(("ai_generated_contents_candidate", "ai_generated_contents"))
    scripts_dir: Path | None = None
    main_path: Path | None = None
    for root in roots:
        candidate = Path(project_root) / root / "scripts" / ontology_name
        mp = candidate / "main.py"
        if mp.is_file():
            scripts_dir = candidate
            main_path = mp
            break
    if scripts_dir is None or main_path is None:
        return False

    package_name = (
        f"_generated_main_kg_{ontology_name}_{abs(hash(str(scripts_dir.resolve())))}"
    )
    for module_name in list(sys.modules):
        if module_name == package_name or module_name.startswith(package_name + "."):
            del sys.modules[module_name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts_dir.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package

    try:
        os.environ["TWA_AGENTIC_DATA_DIR"] = os.path.abspath(str(data_dir))
        spec = importlib.util.spec_from_file_location(f"{package_name}.main", main_path)
        if spec is None or spec.loader is None:
            return False
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{package_name}.main"] = module
        spec.loader.exec_module(module)
        tool = getattr(module, "materialize_hints", None)
        fn = getattr(tool, "fn", tool)
        if fn is None or not callable(fn):
            return False
        result_text = fn(
            doi_hash, entity_safe or entity_label, entity_label, hints_content
        )
        result = json.loads(result_text)
        if str(result.get("status") or "").lower() != "ok":
            logger.warning(
                "    ⚠️  Generated materializer returned non-ok status: %s",
                result.get("message") or result_text,
            )
            return False
        ttl = str(result.get("ttl") or "").strip()
        if not ttl:
            logger.warning("    ⚠️  Generated materializer returned no TTL")
            return False
        os.makedirs(os.path.dirname(intermediate_ttl), exist_ok=True)
        with open(intermediate_ttl, "w", encoding="utf-8") as f:
            f.write(ttl)
        os.makedirs(os.path.dirname(response_file), exist_ok=True)
        with open(response_file, "w", encoding="utf-8") as f:
            f.write(
                f"# Generated Materializer KG Response\n\n**Entity**: {entity_label}\n\n---\n\n"
            )
            f.write(
                json.dumps(
                    {k: v for k, v in result.items() if k != "ttl"},
                    indent=2,
                    ensure_ascii=False,
                )
            )
        logger.info(
            "    ✅ Generated materializer created KG TTL for '%s'", entity_label
        )
        return True
    except Exception as exc:
        logger.warning(
            "    ⚠️  Generated materializer failed for '%s': %s: %s",
            entity_label,
            type(exc).__name__,
            exc,
        )
        return False


async def _process_iterations(
    doi_hash: str,
    config: dict,
    doi_folder: str,
    top_entities: list,
    iterations: list,
    mcp_run_dir: str,
    data_dir: str,
    project_root: str,
    ontology_name: str = "",
    iterations_config_path: str = "",
) -> bool:
    """Async helper to process all iterations and entities."""
    meta_task_config_path = config.get(
        "meta_task_config", "configs/meta_task/meta_task_config.json"
    )
    meta_cfg = load_meta_task_config(meta_task_config_path)
    main_entity_policy = _get_main_entity_kg_policy(meta_cfg)
    agent_model = ((meta_cfg or {}).get("ontologies", {}).get("main", {}) or {}).get(
        "agent_model"
    ) or "gpt-4o"
    output_naming = get_output_naming_config(
        meta_cfg=meta_cfg, ontology_name=ontology_name
    )
    ontology_output_dir = os.path.join(doi_folder, output_naming.output_dir)
    intermediate_ttl_dir = os.path.join(doi_folder, "intermediate_ttl_files")
    os.makedirs(intermediate_ttl_dir, exist_ok=True)
    publish_failures = 0
    runtime_ordered_member_profile = load_all_runtime_ordered_member_profiles(
        meta_cfg=meta_cfg,
        project_root=project_root,
    )
    entity_publish_inputs: Dict[str, Dict[str, object]] = {}
    entity_runtime_reset_done: set[str] = set()
    for entity in top_entities:
        entity_label = entity.get("label", "")
        entity_uri = entity.get("uri", "")
        safe = _safe_name(entity_label)
        entity_publish_inputs[safe] = {
            "entity_label": entity_label,
            "entity_uri": entity_uri,
            "sources": [],
            "hints": {},
            "raw_hints": [],
        }

    # Process iterations 2, 3, 4 (skip iteration 1 - handled by top_entity_kg_building)
    for iteration in iterations:
        iter_num = iteration.get("iteration_number")
        if iter_num == 1:
            continue  # Skip iteration 1

        iter_name = iteration.get("name", f"iteration_{iter_num}")
        kg_building_prompt_path = iteration.get("kg_building_prompt")

        if not kg_building_prompt_path:
            logger.info(f"  ⏭️  No KG building for iteration {iter_num}")
            continue

        # Get MCP configuration
        mcp_set_name = iteration.get("mcp_set_name", "run_created_mcp.json")
        mcp_tools = iteration.get("mcp_tools", ["llm_created_mcp"])

        # Override with test MCP config if provided
        if "test_mcp_config" in config:
            mcp_set_name = config["test_mcp_config"]

        logger.info(f"\n  🔄 Iteration {iter_num}: {iter_name} - KG Building")
        logger.info(f"    MCP Set: {mcp_set_name}, Tools: {mcp_tools}")

        # Load KG building prompt
        kg_prompt = load_prompt(kg_building_prompt_path, project_root)
        if not kg_prompt:
            logger.error(
                f"  ❌ Failed to load KG building prompt for iteration {iter_num}"
            )
            continue

        # Process each entity sequentially with strict isolation
        for idx, entity in enumerate(top_entities):
            entity_label = entity.get("label", "")
            entity_uri = entity.get("uri", "")
            safe = _safe_name(entity_label)

            logger.info(f"  📌 Entity {idx + 1}/{len(top_entities)}: {entity_label}")
            _apply_entity_context_runtime_env(
                main_entity_policy=main_entity_policy, entity_safe=safe
            )

            if safe not in entity_runtime_reset_done:
                _purge_entity_runtime_artifacts(
                    doi_folder=doi_folder,
                    entity_label=entity_label,
                    entity_safe=safe,
                    entity_uri=entity_uri,
                    ontology_output_dir=ontology_output_dir,
                    intermediate_ttl_dir=intermediate_ttl_dir,
                )
                _purge_entity_canonical_persistence(
                    doi_folder=doi_folder,
                    entity_label=entity_label,
                    entity_safe=safe,
                    entity_uri=entity_uri,
                )
                entity_runtime_reset_done.add(safe)

            response_file = os.path.join(
                doi_folder, "responses", f"iter{iter_num}_kg_building", f"{safe}.md"
            )
            intermediate_ttl = os.path.join(
                intermediate_ttl_dir, f"iteration_{iter_num}_{safe}.ttl"
            )
            hints_file = _find_hints_file(
                mcp_run_dir=mcp_run_dir, iter_num=iter_num, entity_safe=safe
            )
            freshness_deps = [
                iterations_config_path,
                kg_building_prompt_path,
                hints_file,
                __file__,
            ]
            hints_content = ""

            # Publish-time hint reconciliation needs the merged structured hints even
            # when we can reuse existing KG-building artifacts for this iteration.
            if not os.path.exists(hints_file):
                logger.warning(f"    ⚠️  Hints file not found: {hints_file}")
                continue

            try:
                with open(hints_file, "r", encoding="utf-8") as f:
                    hints_content = f.read()
            except Exception as e:
                logger.error(f"    ❌ Failed to read hints file: {e}")
                continue

            patch_files = _find_enrichment_patch_files(
                mcp_run_dir=mcp_run_dir,
                iter_num=iter_num,
                entity_safe=safe,
            )
            if patch_files:
                patch_chunks: list[str] = []
                for patch_file in patch_files:
                    try:
                        with open(patch_file, "r", encoding="utf-8") as f:
                            patch_text = f.read().strip()
                    except Exception as e:
                        logger.warning(
                            f"    ⚠️  Failed to read enrichment patch {patch_file}: {e}"
                        )
                        continue
                    if patch_text:
                        patch_chunks.append(
                            f"\n\nSECTION: ENRICHMENT_PATCH_FROM {os.path.basename(patch_file)}\n{patch_text}"
                        )
                if patch_chunks:
                    hints_content = hints_content.rstrip() + "".join(patch_chunks)
                    freshness_deps.extend(patch_files)
                    logger.info(
                        f"    🧩 Merged {len(patch_chunks)} enrichment patch file(s) into iter{iter_num} hints"
                    )

            aggregated_hints = entity_publish_inputs.get(safe, {}).get("hints", {})
            if isinstance(aggregated_hints, dict):
                entity_publish_inputs[safe]["hints"] = _merge_hint_payloads(
                    aggregated_hints,
                    _load_structured_hints(hints_content),
                )
            raw_hints = entity_publish_inputs.get(safe, {}).get("raw_hints")
            if isinstance(raw_hints, list):
                raw_hints.append(hints_content)

            # Reuse only if the prior response + intermediate TTL are newer than the
            # current iterations config, KG prompt, and extracted hints.
            if _artifact_is_current(
                response_file, freshness_deps, project_root=project_root
            ) and _artifact_is_current(
                intermediate_ttl, freshness_deps, project_root=project_root
            ):
                logger.info(f"    ⏭️  KG building already completed")
                sources = entity_publish_inputs.get(safe, {}).get("sources", [])
                if isinstance(sources, list) and intermediate_ttl not in sources:
                    sources.append(intermediate_ttl)
                continue
            if os.path.exists(response_file) or os.path.exists(intermediate_ttl):
                logger.info(
                    f"    🔁 Existing KG building artifacts are stale for '{entity_label}', regenerating"
                )

            # Run KG building agent
            try:
                # Semantic MCP loop / tests can force the ReAct MCP path instead of
                # the deterministic materialize_hints short-circuit.
                force_react_kg = bool(
                    config.get("force_react_kg") or config.get("skip_materialize_hints")
                )
                generated_materialized = False
                if not force_react_kg:
                    generated_materialized = _try_generated_materialize_hints(
                        doi_hash=doi_hash,
                        data_dir=data_dir,
                        ontology_name=ontology_name,
                        project_root=project_root,
                        entity_label=entity_label,
                        entity_safe=safe,
                        hints_content=hints_content,
                        intermediate_ttl=intermediate_ttl,
                        response_file=response_file,
                    )
                elif force_react_kg:
                    logger.info(
                        "    🔁 force_react_kg enabled — skipping materialize_hints short-circuit"
                    )
                if not generated_materialized:
                    response = await run_kg_building_agent(
                        doi_hash=doi_hash,
                        entity_label=entity_label,
                        entity_uri=entity_uri,
                        hints_content=hints_content,
                        kg_prompt=kg_prompt,
                        iter_num=iter_num,
                        mcp_tools=mcp_tools,
                        mcp_set_name=mcp_set_name,
                        data_dir=data_dir,
                        main_entity_policy=main_entity_policy,
                        agent_model=agent_model,
                    )

                # Copy output.ttl to intermediate TTL file
                # In test mode, look for entity-specific TTL in `{ontology_name}_output/` (stale fallback only).
                test_mode = "test_mcp_config" in config
                if test_mode:
                    # Test mode MUST still prefer the canonical MCP persistence locations (memory/ + exports/)
                    # over any previously-published {ontology}_output snapshots, otherwise we can "lock in"
                    # an early (iter2) TTL that lacks later additions like steps.
                    found = False

                    # 1) Prefer memory/ (canonical; should contain the latest merged graph)
                    mem_dir = os.path.join(doi_folder, "memory")
                    mem_candidates = [
                        os.path.join(mem_dir, f"{safe}.ttl"),
                        os.path.join(mem_dir, f"{safe.lower()}.ttl"),
                        os.path.join(mem_dir, f"{entity_label}.ttl"),
                    ]
                    for mem_path in mem_candidates:
                        if os.path.exists(mem_path):
                            shutil.copy2(mem_path, intermediate_ttl)
                            logger.info(
                                f"    ✅ [TEST MODE] Saved from memory/{os.path.basename(mem_path)}"
                            )
                            found = True
                            break

                    # 2) Fallback to latest exports snapshot (if any)
                    if not found:
                        if _try_copy_entity_ttl_to_intermediate(
                            doi_folder=doi_folder,
                            entity_label=entity_label,
                            entity_safe=safe,
                            entity_uri=entity_uri,
                            intermediate_ttl=intermediate_ttl,
                        ):
                            found = True

                    # 3) Last resort: previously published output dir (may be stale)
                    if not found:
                        test_output_dir = os.path.join(
                            doi_folder, f"{ontology_name}_output"
                        )
                        entity_slug = (
                            entity_label.lower().replace(" ", "-").replace("_", "-")
                        )
                        test_candidates = [
                            os.path.join(test_output_dir, f"{safe}.ttl"),
                            os.path.join(test_output_dir, f"{entity_slug}.ttl"),
                            os.path.join(test_output_dir, f"{safe.lower()}.ttl"),
                            os.path.join(test_output_dir, f"{entity_label}.ttl"),
                        ]
                        for candidate in test_candidates:
                            if os.path.exists(candidate):
                                shutil.copy2(candidate, intermediate_ttl)
                                logger.info(
                                    f"    ✅ [TEST MODE] Saved from {os.path.basename(candidate)} (stale fallback)"
                                )
                                found = True
                                break

                    if not found:
                        logger.warning(
                            f"    ⚠️  [TEST MODE] No TTL found (memory/exports/output) for {entity_label}"
                        )
                else:
                    found = _try_copy_entity_ttl_to_intermediate(
                        doi_folder=doi_folder,
                        entity_label=entity_label,
                        entity_safe=safe,
                        entity_uri=entity_uri,
                        intermediate_ttl=intermediate_ttl,
                    )
                    if not found:
                        publish_failures += 1
                if found:
                    step_ok, step_msgs = _validate_ordered_members_against_hints(
                        ttl_path=intermediate_ttl,
                        hints_content=hints_content,
                        main_entity_policy=main_entity_policy,
                    )
                    if not step_ok:
                        for msg in step_msgs:
                            logger.warning(
                                f"    ⚠️  Ordered-step hint fidelity failed: {msg}"
                            )
                        try:
                            os.remove(intermediate_ttl)
                        except Exception:
                            pass
                        raise RuntimeError(
                            "KG output failed ordered-step hint fidelity validation: "
                            + "; ".join(step_msgs[:5])
                        )
                    sources = entity_publish_inputs.get(safe, {}).get("sources", [])
                    if isinstance(sources, list) and intermediate_ttl not in sources:
                        sources.append(intermediate_ttl)

            except Exception as e:
                logger.error(f"    ❌ KG building failed for '{entity_label}': {e}")
                continue

            # CRITICAL: Synchronization point between entities
            # Wait to ensure all MCP server file operations are flushed to disk
            # before moving to next entity (which will overwrite global state)
            if idx < len(top_entities) - 1:  # Not the last entity
                logger.info(
                    f"    🔒 Entity synchronization point (preparing for next entity)..."
                )
                await asyncio.sleep(2)
                logger.info(f"    ✅ Ready for next entity")

    # Publish once per entity from the accumulated iteration shards.
    for safe, info in entity_publish_inputs.items():
        entity_label = str(info.get("entity_label") or safe)
        entity_uri = str(info.get("entity_uri") or "")
        sources = info.get("sources") or []
        if not isinstance(sources, list) or not sources:
            logger.warning(
                f"    ⚠️  No intermediate TTLs accumulated for {entity_label}"
            )
            publish_failures += 1
            continue
        aggregated_hints = info.get("hints") or {}
        raw_hints = info.get("raw_hints") or []

        published = publish_ttl(
            doi_hash=doi_hash,
            ontology_name=ontology_name,
            entity_safe=safe,
            entity_uri=entity_uri,
            entity_label=entity_label,
            data_dir=data_dir,
            meta_cfg=meta_cfg,
            src_candidates=sources,
        )
        if published:
            parameter_property_namespace_iri = ""
            for spec in _get_hint_reconciliation_specs(main_entity_policy):
                parameter_property_namespace_iri = str(
                    spec.get("property_namespace_iri") or ""
                ).strip()
                if parameter_property_namespace_iri:
                    break
            if not parameter_property_namespace_iri:
                shell_validation = (main_entity_policy or {}).get(
                    "shell_validation", {}
                ) or {}
                parameter_property_namespace_iri = _namespace_iri(
                    str(shell_validation.get("top_entity_class_iri") or "")
                )
            repaired, repair_msgs = _repair_published_entity_ttl(
                ttl_path=published,
                doi_folder=doi_folder,
                ontology_name=ontology_name,
                entity_uri=entity_uri,
                entity_label=entity_label,
                meta_cfg=meta_cfg,
                main_entity_policy=main_entity_policy,
            )
            if repair_msgs:
                for msg in repair_msgs:
                    logger.info(f"    🔧 {msg}")
            if not repaired:
                logger.error("    ❌ Published TTL repair failed:")
                for msg in repair_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1
            hints_repaired, hints_msgs = _repair_published_entity_ttl_from_hints(
                ttl_path=published,
                entity_uri=entity_uri,
                entity_label=entity_label,
                aggregated_hints=aggregated_hints
                if isinstance(aggregated_hints, dict)
                else {},
                ontology_name=ontology_name,
                main_entity_policy=main_entity_policy,
            )
            if hints_msgs:
                for msg in hints_msgs:
                    logger.info(f"    🧭 {msg}")
            if not hints_repaired:
                logger.error("    ❌ Published TTL hint reconciliation failed:")
                for msg in hints_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1

            parameter_repaired, parameter_msgs = (
                _repair_generic_parameter_quantity_hints(
                    ttl_path=published,
                    raw_hints=raw_hints if isinstance(raw_hints, list) else [],
                    ontology_name=ontology_name,
                    property_namespace_iri=parameter_property_namespace_iri,
                )
            )
            if parameter_msgs:
                for msg in parameter_msgs:
                    logger.info(f"    🧪 {msg}")
            if not parameter_repaired:
                logger.error("    ❌ Published TTL generic parameter repair failed:")
                for msg in parameter_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1

            pruned, prune_msgs = _prune_unhinted_orphan_required_targets(
                ttl_path=published,
                raw_hints=raw_hints if isinstance(raw_hints, list) else [],
                main_entity_policy=main_entity_policy,
            )
            if prune_msgs:
                for msg in prune_msgs:
                    logger.info(f"    🧹 {msg}")
            if not pruned:
                logger.error("    ❌ Published TTL orphan pruning failed:")
                for msg in prune_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1

            hygiene_ok, hygiene_msgs = enforce_published_graph_hygiene_file(
                ttl_path=published,
                top_entity_uri=entity_uri,
                top_class_iri=str(
                    ((main_entity_policy or {}).get("shell_validation", {}) or {}).get(
                        "top_entity_class_iri"
                    )
                    or ""
                ),
                entity_label=entity_label,
            )
            if hygiene_msgs:
                for msg in hygiene_msgs:
                    logger.info(f"    🧼 {msg}")
            if not hygiene_ok:
                logger.error("    ❌ Published TTL graph hygiene enforcement failed:")
                for msg in hygiene_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1

            ordered_ok, ordered_report = enforce_ordered_member_integrity_file(
                ttl_path=published,
                runtime_profile=runtime_ordered_member_profile,
                top_entity_uri=entity_uri,
            )
            ordered_status = str((ordered_report or {}).get("status") or "skipped")
            ordered_msgs = (ordered_report or {}).get("messages") or []
            if ordered_status == "repaired":
                logger.info("    ✅ Ordered-member integrity repaired at publish stage")
            elif ordered_status == "no_action":
                logger.info("    ✅ Ordered-member integrity already satisfied")
            elif ordered_status == "skipped":
                logger.info(
                    "    ℹ️  Ordered-member integrity enforcement skipped (no T-Box contract)"
                )
            if ordered_msgs:
                for msg in ordered_msgs:
                    logger.info(f"    ↳ {msg}")
            if not ordered_ok:
                logger.error("    ❌ Ordered-member integrity enforcement failed:")
                for msg in ordered_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1

            hygiene_ok, hygiene_msgs = enforce_published_graph_hygiene_file(
                ttl_path=published,
                top_entity_uri=entity_uri,
                top_class_iri=str(
                    ((main_entity_policy or {}).get("shell_validation", {}) or {}).get(
                        "top_entity_class_iri"
                    )
                    or ""
                ),
                entity_label=entity_label,
            )
            if hygiene_msgs:
                for msg in hygiene_msgs:
                    logger.info(f"    🧼 {msg}")
            if not hygiene_ok:
                logger.error(
                    "    ❌ Published TTL final graph hygiene enforcement failed:"
                )
                for msg in hygiene_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1

            logger.info(
                f"    ✅ Published entity TTL: {os.path.relpath(published, doi_folder)}"
            )
            ok_struct, struct_msgs = _validate_entity_ttl_structure(
                ttl_path=published,
                entity_uri=entity_uri,
                entity_label=entity_label,
                main_entity_policy=main_entity_policy,
            )
            if not ok_struct:
                logger.error("    ❌ Published TTL failed structural validation:")
                for msg in struct_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1
            else:
                post_publish_warnings = (main_entity_policy or {}).get(
                    "post_publish_warnings", {}
                ) or {}
                _warn_typed_nodes_missing_configured_predicates(
                    ttl_path=published,
                    entity_label=entity_label,
                    warning_rules=post_publish_warnings.get(
                        "typed_nodes_missing_predicate", []
                    )
                    or [],
                )
        else:
            logger.warning(f"    ⚠️  Failed to publish entity TTL for {entity_label}")
            publish_failures += 1

    if publish_failures:
        logger.warning(
            f"⚠️  Main KG building finished with {publish_failures} publish/copy failure(s)"
        )
        return False
    return True


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main KG Building step: Build knowledge graphs for iterations 2, 3, and 4.

    Args:
        doi_hash: DOI hash for the paper
        config: Pipeline configuration dictionary

    Returns:
        True if KG building succeeded
    """
    # Extract config parameters
    data_dir = config.get("data_dir", "data")
    project_root = config.get("project_root", ".")
    os.environ["TWA_AGENTIC_DATA_DIR"] = os.path.abspath(str(data_dir))

    logger.info(f"🏗️  Starting main KG building for DOI: {doi_hash}")

    doi_folder = os.path.join(data_dir, doi_hash)
    if not os.path.exists(doi_folder):
        logger.error(f"DOI folder not found: {doi_folder}")
        return False

    mcp_run_dir = os.path.join(doi_folder, "mcp_run")

    # Check if step is already completed
    marker_file = os.path.join(doi_folder, ".main_kg_building_done")
    if os.path.exists(marker_file):
        # Marker can exist even if publishing failed in older runs.
        # If no per-entity TTLs are present in the published output dir, rerun.
        meta_task_config_path = config.get(
            "meta_task_config", "configs/meta_task/meta_task_config.json"
        )
        meta_cfg = load_meta_task_config(meta_task_config_path)
        ontology_name = (
            config.get("ontology_name")
            or get_main_ontology_name(meta_cfg, default="main")
        ).strip() or "main"
        naming = get_output_naming_config(
            meta_cfg=meta_cfg, ontology_name=ontology_name
        )
        published_dir = os.path.join(doi_folder, naming.output_dir)
        try:
            published_entity_ttls = (
                [
                    f
                    for f in os.listdir(published_dir)
                    if f.lower().endswith(".ttl") and f.lower() != "top.ttl"
                ]
                if os.path.isdir(published_dir)
                else []
            )
        except Exception:
            published_entity_ttls = []
        if published_entity_ttls:
            latest_hint_mtime = 0.0
            try:
                if os.path.isdir(mcp_run_dir):
                    for fname in os.listdir(mcp_run_dir):
                        low = fname.lower()
                        if (
                            low.startswith("iter")
                            and "_hints_" in low
                            and low.endswith(".txt")
                        ):
                            latest_hint_mtime = max(
                                latest_hint_mtime,
                                os.path.getmtime(os.path.join(mcp_run_dir, fname)),
                            )
            except Exception:
                latest_hint_mtime = 0.0
            config_mtime = 0.0
            try:
                if meta_task_config_path and os.path.exists(meta_task_config_path):
                    config_mtime = os.path.getmtime(meta_task_config_path)
            except Exception:
                config_mtime = 0.0
            step_code_mtime = 0.0
            try:
                if __file__ and os.path.exists(__file__):
                    step_code_mtime = os.path.getmtime(__file__)
            except Exception:
                step_code_mtime = 0.0
            latest_required_mtime = max(
                latest_hint_mtime, config_mtime, step_code_mtime
            )

            published_paths = [
                os.path.join(published_dir, f) for f in published_entity_ttls
            ]
            published_are_current = True
            if latest_required_mtime > 0:
                try:
                    published_are_current = all(
                        os.path.getmtime(p) >= latest_required_mtime
                        for p in published_paths
                    )
                except Exception:
                    published_are_current = False

            if published_are_current:
                logger.info(f"  ⏭️  Main KG building already completed (marker exists)")
                return True
            logger.warning(
                "  🔁 Marker exists but published entity TTLs are older than current hints/config; re-running main KG building"
            )
        else:
            logger.warning(
                "  🔁 Marker exists but no published entity TTLs found; re-running main KG building"
            )

    # Load top entities
    entities_path = os.path.join(mcp_run_dir, "iter1_top_entities.json")
    if not os.path.exists(entities_path):
        logger.error(f"Top entities file not found: {entities_path}")
        return False

    try:
        with open(entities_path, "r", encoding="utf-8") as f:
            top_entities = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load top entities: {e}")
        return False

    meta_task_config_path = config.get(
        "meta_task_config", "configs/meta_task/meta_task_config.json"
    )
    meta_cfg = load_meta_task_config(meta_task_config_path)
    ontology_name = (
        config.get("ontology_name") or get_main_ontology_name(meta_cfg, default="main")
    ).strip() or "main"
    shell_validation = (
        _get_main_entity_kg_policy(meta_cfg).get("shell_validation", {}) or {}
    )
    top_class_iri = str(shell_validation.get("top_entity_class_iri") or "").strip()
    supplemented_top_entities = _supplement_top_entities_from_txt(
        doi_folder,
        top_entities,
        top_class_iri,
    )
    if len(supplemented_top_entities) > len(top_entities or []):
        logger.warning(
            "Supplemented top entities from top_entities.txt: %s -> %s",
            len(top_entities or []),
            len(supplemented_top_entities),
        )
    top_entities = supplemented_top_entities
    top_entities = _canonicalize_top_entities(
        top_entities=top_entities,
        doi_folder=doi_folder,
        ontology_name=ontology_name,
        meta_cfg=meta_cfg,
    )
    if not top_entities:
        logger.warning("No top entities found")
        return False

    logger.info(f"Found {len(top_entities)} top entities")
    published_top_ttl = _ensure_published_top_shell(
        doi_hash=doi_hash,
        doi_folder=doi_folder,
        ontology_name=ontology_name,
        meta_cfg=meta_cfg,
        data_dir=data_dir,
    )
    if not published_top_ttl:
        logger.error("Top shell TTL not found for merge")
        return False
    try:
        with open(entities_path, "w", encoding="utf-8") as f:
            json.dump(top_entities, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to persist canonicalized top entities: {e}")

    # Load iterations config
    # Determine ontology name.
    # Prefer an explicit config override, otherwise derive from meta_task_config.

    # Candidate-first resolution (supports repos without ai_generated_contents/)
    iterations_config_path = resolve_generated_file(
        f"ai_generated_contents/iterations/{ontology_name}/iterations.json",
        project_root=project_root,
    )
    if not os.path.exists(iterations_config_path):
        logger.error(f"Iterations config not found: {iterations_config_path}")
        return False

    try:
        with open(iterations_config_path, "r", encoding="utf-8") as f:
            iterations_config = json.load(f)
        iterations = iterations_config.get("iterations", [])
    except Exception as e:
        logger.error(f"Failed to load iterations config: {e}")
        return False

    # Process all iterations and entities with proper async handling
    try:
        success = asyncio.run(
            _process_iterations(
                doi_hash=doi_hash,
                config=config,
                doi_folder=doi_folder,
                top_entities=top_entities,
                iterations=iterations,
                mcp_run_dir=mcp_run_dir,
                data_dir=data_dir,
                project_root=project_root,
                ontology_name=ontology_name,
                iterations_config_path=iterations_config_path,
            )
        )

        if not success:
            logger.error(f"  ❌ Iteration processing failed")
            return False
    except Exception as e:
        logger.error(f"  ❌ Iteration processing raised exception: {e}")
        import traceback

        traceback.print_exc()
        return False

    # Create completion marker
    try:
        with open(marker_file, "w") as f:
            f.write("completed\n")
        logger.info(f"  📌 Created completion marker")
    except Exception as e:
        logger.warning(f"  ⚠️  Failed to create completion marker: {e}")

    logger.info(f"✅ Main KG building completed for DOI: {doi_hash}")
    return True


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m src.pipelines.main_kg_building.build <doi_hash>")
        sys.exit(1)

    # Create config dict for standalone usage
    config = {"data_dir": "data", "project_root": "."}

    success = run_step(sys.argv[1], config)
    sys.exit(0 if success else 1)
