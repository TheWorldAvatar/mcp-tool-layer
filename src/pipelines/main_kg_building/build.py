"""
Main KG Building Module

Handles knowledge graph building for iterations 2, 3, and 4.
Uses BaseAgent with MCP tools to build TTL files from extraction hints.
"""

import os
import json
import asyncio
import shutil
import sys
import tempfile
import hashlib
import base64
import re
import unicodedata
from pathlib import Path
from filelock import FileLock
from typing import Any, Dict, List, Optional
from rdflib import BNode, Graph, URIRef, Literal, Namespace
from rdflib.namespace import RDF, RDFS

from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from src.pipelines.utils.atomic_replace import replace_with_retry
from src.pipelines.utils.llm_transport_retry import (
    is_llm_transport_error,
    retry_async_on_transport,
)
from src.pipelines.utils.ttl_publisher import (
    enforce_published_graph_hygiene_file,
    get_main_ontology_name,
    get_output_naming_config,
    load_meta_task_config,
    publish_top_ttl,
    publish_ttl,
)
from src.pipelines.utils.ordered_member_integrity import (
    enforce_ordered_member_integrity_file,
    load_all_runtime_ordered_member_profiles,
)
from src.pipelines.utils.top_entity_identity import (
    entity_artifact_name,
    entity_scope_name,
    hydrate_and_validate_top_entity_types,
    load_selected_top_class,
    persist_entity_identity_sidecars,
)
from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_ontology_publish_contract,
)
from src.agents.scripts_and_prompts_generation.llm_global_context_resolver import (
    inject_global_context_brief,
    load_global_context_brief,
)
from src.agents.scripts_and_prompts_generation.fixed_om2_runtime import (
    OM2_UNIT_MAP,
    find_or_create_om2_quantity_from_label,
)
from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import (
    publish_reusable_entities_to_central_memory,
    publish_reusable_entities_to_document_memory,
)
from src.agents.scripts_and_prompts_generation.reuse_policy import (
    reuse_storage_scope,
)
from src.agents.scripts_and_prompts_generation.llm_semantic_abox_judge import (
    judge_semantic_abox,
)
from src.agents.scripts_and_prompts_generation.llm_framework_integrity_judge import (
    judge_framework_integrity,
)
from src.agents.scripts_and_prompts_generation.presence_coverage_judge import (
    catalog_for_groups,
    format_presence_coverage_feedback,
    judge_presence_coverage,
)
from src.agents.scripts_and_prompts_generation.presence_tool_recipe_judge import (
    extract_tool_inventory,
    format_tool_recipe_feedback,
    propose_tool_recipes,
)
from src.agents.scripts_and_prompts_generation.llm_iteration_continuity_judge import (
    judge_iteration_continuity,
)
from src.pipelines.utils.kg_revision_limits import (
    apply_disable_kg_revisions,
    ensure_kg_norev,
)
from src.pipelines.utils.kg_full_hints_onepass import (
    build_generic_onepass_kg_prompt,
    build_mcp_native_onepass_task_prompt,
    build_mcp_native_onepass_user_aligned_task_prompt,
    build_mcp_semantic_surface_task_prompt,
    build_onepass_kg_prompt,
    collapse_kg_iterations_for_full_hints_onepass,
    combine_hint_ledgers,
    resolve_generated_mcp_relationship_contract,
    resolve_generated_mcp_tool_surface,
)

logger = get_logger("pipeline", "MainKGBuilding")
OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")
_SEMANTIC_HINT_MARKER = "SEMANTIC_HINTS_V1"
MAIN_KG_MAX_TOOL_CALLS = 400
MAIN_KG_RECURSION_LIMIT = (MAIN_KG_MAX_TOOL_CALLS * 2) + 2
MAIN_KG_ONEPASS_MAX_TOOL_CALLS = 800
MAIN_KG_ONEPASS_RECURSION_LIMIT = (MAIN_KG_ONEPASS_MAX_TOOL_CALLS * 2) + 2


def _is_semantic_hint_content(content: str) -> bool:
    return str(content or "").lstrip().startswith(_SEMANTIC_HINT_MARKER)


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


def _resolve_kg_attempt_limit(policy: dict[str, Any] | None) -> int:
    """How many full KG rebuilds to run. Default 1: keep the first persisted graph."""
    policy = policy or {}
    raw = policy.get("max_attempts")
    if raw is None:
        nested = policy.get("presence_coverage_audit") or {}
        if isinstance(nested, dict):
            raw = nested.get("max_attempts")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _resolve_kg_audit_policy(policy: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Choose the KG audit. Default is presence coverage; expensive FI is opt-in."""
    policy = policy or {}
    raw = dict(policy.get("presence_coverage_audit") or {})
    legacy = bool(policy.get("legacy_llm_framework_integrity")) or bool(
        raw.get("legacy_llm_audits")
    )
    if legacy:
        return raw, True
    cfg = {
        "enabled": True if raw.get("enabled") is None else bool(raw.get("enabled")),
        "replace_llm_audits": (
            True
            if raw.get("replace_llm_audits") is None
            else bool(raw.get("replace_llm_audits"))
        ),
        "model": str(raw.get("model") or "gpt-4o"),
        "mcp_groups": list(raw.get("mcp_groups") or []),
    }
    for key, value in raw.items():
        if key not in cfg:
            cfg[key] = value
    return cfg, False


def _resolve_continuity_audit_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    """Cross-iteration continuity judge. Default on; set enabled=false to skip."""
    policy = policy or {}
    raw = dict(policy.get("continuity_audit") or {})
    return {
        "enabled": True if raw.get("enabled") is None else bool(raw.get("enabled")),
        **{key: value for key, value in raw.items() if key != "enabled"},
    }


def _hint_fidelity_audit_enabled(policy: dict[str, Any] | None) -> bool:
    """Deterministic JSON hint-fidelity check. Default on; set enabled=false to skip."""
    policy = policy or {}
    raw = policy.get("hint_fidelity_audit")
    if not isinstance(raw, dict):
        return True
    if raw.get("enabled") is None:
        return True
    return bool(raw.get("enabled"))


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
    """Resolve a top entity without overriding an explicit scoped URI."""
    explicit_uri = str(entity_uri or "").strip()
    if explicit_uri:
        return explicit_uri

    class_iri = str(top_class_iri or "").strip()
    typed_entities = [
        s
        for s in g.subjects(RDF.type, URIRef(class_iri))
        if class_iri and isinstance(s, URIRef)
    ]
    if not typed_entities:
        return ""

    if len(typed_entities) == 1:
        return str(typed_entities[0])
    return ""


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
            return _parse_turtle_path(path)
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
    ontology_contract: Optional[dict] = None,
) -> list:
    top_class_iri = _ontology_contract_top_class_iri(ontology_contract)
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
    identity = "\0".join(
        (
            str(top_class_iri or "").strip(),
            " ".join(str(label or "").casefold().split()),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).digest()[:12]
    token = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    class_local = _local_name(top_class_iri) or "TopEntity"
    return f"https://www.theworldavatar.com/kg/instance/{class_local}/{token}"


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


def _preferred_ordered_member_targets(
    g: Graph, *, target_cls: URIRef, order_predicate_iri: str
) -> list[URIRef]:
    """Choose one concrete node per semantic member, preferring richer subclassed nodes."""
    if not str(order_predicate_iri or "").strip():
        return []
    order_pred = URIRef(order_predicate_iri)
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
    publish_policy = (main_entity_policy or {}).get("publish", {}) or {}
    hint_reconciliation = publish_policy.get("hint_reconciliation", {}) or {}
    optional_links = hint_reconciliation.get("optional_links", []) or []

    specs: list[dict] = []
    for raw_spec in optional_links:
        pred_iri = str((raw_spec or {}).get("predicate_iri") or "").strip()
        target_class_iri = str((raw_spec or {}).get("target_class_iri") or "").strip()
        property_namespace_iri = str(
            (raw_spec or {}).get("property_namespace_iri") or ""
        ).strip()
        allowed_scalar_property_iris = [
            str(value).strip()
            for value in (raw_spec or {}).get("allowed_scalar_property_iris", [])
            if str(value).strip()
        ]
        order_predicate_iri = str(
            (raw_spec or {}).get("order_predicate_iri") or ""
        ).strip()
        if not (pred_iri and target_class_iri):
            continue
        specs.append(
            {
                "section_name": str((raw_spec or {}).get("section_name") or "").strip()
                or _local_name(target_class_iri),
                "predicate_iri": pred_iri,
                "target_class_iri": target_class_iri,
                "property_namespace_iri": property_namespace_iri,
                "allowed_scalar_property_iris": allowed_scalar_property_iris,
                "order_predicate_iri": order_predicate_iri,
                "optional": True,
                "ordered_member": bool((raw_spec or {}).get("ordered_member")),
                "prune_unhinted_scalar_properties": bool(
                    (raw_spec or {}).get("prune_unhinted_scalar_properties")
                ),
            }
        )
    return specs


def _ontology_contract_top_class_iri(ontology_contract: Optional[dict]) -> str:
    """Return only an explicitly machine-declared top-role class."""
    contract = ontology_contract or {}
    for key in ("top_role", "top_entity"):
        role = contract.get(key)
        if not isinstance(role, dict):
            continue
        class_iri = str(role.get("class_iri") or role.get("class") or "").strip()
        if class_iri and str(role.get("source") or "").strip() in {
            "tbox",
            "owl_restriction",
            "machine",
        }:
            return class_iri
    return ""


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
    *,
    main_entity_policy: dict,
    entity_safe: str,
    entity_uri: str = "",
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
    os.environ["TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI"] = str(entity_uri or "").strip()


def _augment_kg_prompt_with_runtime_rules(
    *,
    kg_prompt: str,
    entity_label: str,
    entity_uri: str,
    doi_hash: str,
    main_entity_policy: dict,
    hints_content: str = "",
    ontology_contract: Optional[dict] = None,
    compiled_iteration_spec: Optional[dict] = None,
) -> str:
    prompt_rules = (main_entity_policy or {}).get("prompt_rules", {}) or {}
    om2_unit_aliases = ", ".join(sorted(OM2_UNIT_MAP))
    ordered_profile = (ontology_contract or {}).get("ordered_member_profile", {}) or {}
    ordering_locals = [
        str(value).strip()
        for value in ordered_profile.get("single_valued_ordering_properties", []) or []
        if str(value).strip()
    ]
    object_property_locals = {
        _local_name(str(item.get("property_iri") or ""))
        for item in (ontology_contract or {}).get("object_properties", []) or []
        if isinstance(item, dict) and str(item.get("property_iri") or "").strip()
    }
    lexical_object_fields: set[str] = set()
    try:
        hint_payload = json.loads(_strip_code_fences(hints_content))
    except (TypeError, json.JSONDecodeError):
        hint_payload = {}
    if isinstance(hint_payload, dict):
        for entity in hint_payload.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            datatype_properties = entity.get("datatype_properties")
            if not isinstance(datatype_properties, dict):
                continue
            lexical_object_fields.update(
                property_local
                for raw_property in datatype_properties
                if (property_local := _local_name(str(raw_property)))
                in object_property_locals
            )
    canonical_scope = entity_scope_name(entity_label, entity_uri)
    lines: list[str] = [
        "Generic MCP execution rules (authoritative for every KG-building run):",
        "- Treat the exposed MCP tool inventory and schemas as the only callable API. Never invent a tool name, parameter name, positional argument, or capability mentioned only in prose.",
        "- Inspect each tool schema before calling it and pass exactly its declared arguments. If the required operation is not exposed, stop with a structured failure instead of guessing.",
        "- `init_memory` only opens or resumes the current DOI/entity scope and is idempotent. It has no reset mode. Call it before mutation when needed; repeated calls must never be used to clear state.",
        f"- The canonical entity scope is `{canonical_scope}`. Pass this exact value as `top_level_entity_name` to `init_memory` and `export_memory`; labels and shortened aliases are not valid scopes.",
        "- A transport-level successful tool call is not a semantic success. Any structured result with `ok=false` or status `rejected`, `error`, or `failed` must be treated as failure and corrected before continuing.",
        "- Ensure the scoped top entity exists in the active graph with its required T-Box-derived type before adding relationships to it. Reuse its supplied IRI and do not create a competing root.",
        "- Before creating or linking an entity that may have been produced by an earlier iteration, call the corresponding exposed `check_existing_<Class>` tool. A scoped prior-iteration result may be resolved directly when its identity matches the current ref. A central-memory candidate may be reused only when that candidate includes a `reuse_authorization_token`; pass that exact token to every relationship call that links the candidate. A candidate without such a token is lookup evidence only and is not authorized for reuse; create a new grounded entity when the current hints require one.",
        "- Resolve every child IRI from a successful creator result or persisted graph evidence. Never guess UUIDs, use labels as IRIs, or pass unresolved local references to relationship tools.",
        "- Every OM-2 quantity is an occurrence-local relationship target. Create a fresh quantity for each distinct (owner IRI, predicate IRI) slot; never share a Temperature, Duration, Pressure, Volume, rate, or other quantity IRI across different owners, even when class, label, numerical value, and unit are identical. Reuse an existing quantity only for the exact same owner and predicate already persisted in scoped memory.",
        f"- For OM-2 quantity tools, follow the exposed tool schema and use one of these runtime-supported unit aliases: {om2_unit_aliases}. Preserve any source quantity that the tool can represent; if a value or unit is rejected, correct it from source evidence or omit that unsupported assertion rather than guessing.",
        "- If a mutation returns `OBJECT_OCCURRENCE_REUSE_FORBIDDEN`, permanently discard the rejected object IRI for that requested owner slot. Follow the structured recovery payload and create a fresh occurrence; never retry the same object IRI or rebuild unrelated graph scope.",
        "- Materialize the meaning of all supplied extraction evidence regardless of whether that evidence is JSON, text, nested, flat, ID-based, label-based, or reference-based. Representation is not a reason to omit a grounded fact.",
        "- Before export, verify that all attempted writes succeeded and that the active graph contains substantive A-Box facts for this scope. Do not export or claim success for a T-Box-only or empty A-Box graph.",
        "- The final MCP tool call must be `export_memory`, after all validation and mutation calls. Do not call any tool after it. Its successful result must contain non-empty A-Box Turtle and the scoped top entity; otherwise stop with a structured failure.",
    ]
    compiled_classes, compiled_properties = _compiled_iteration_owned_surface(
        compiled_iteration_spec
    )
    if compiled_iteration_spec is not None:
        linked_classes = {
            str(value).strip()
            for value in compiled_iteration_spec.get(
                "linked_materialization_classes", []
            )
            or []
            if str(value).strip()
        }
        lines.extend(
            [
                "- Compiled iteration ownership is authoritative; do not infer ownership from "
                "prompt wording.",
                "- Compiled iteration-owned classes: ["
                + ", ".join(sorted(compiled_classes))
                + "].",
                "- Compiled iteration-owned object properties: ["
                + ", ".join(sorted(compiled_properties))
                + "].",
                "- Compiled linked materialization classes: ["
                + ", ".join(sorted(linked_classes))
                + "].",
            ]
        )
        object_contracts = {
            _local_name(str(item.get("property_iri") or "")): item
            for item in (ontology_contract or {}).get("object_properties", []) or []
            if isinstance(item, dict)
            and str(item.get("property_iri") or "").strip()
        }
        for property_local in sorted(compiled_properties):
            property_contract = object_contracts.get(property_local) or {}
            range_locals = sorted(
                {
                    _local_name(str(value))
                    for value in property_contract.get("range_iris") or []
                    if str(value).strip()
                }
            )
            if range_locals:
                lines.append(
                    f"- T-Box relationship target for `{property_local}`: "
                    f"[{', '.join(range_locals)}]. Follow the closed MCP schemas: when an "
                    "atomic creator owns this target or edge, pass its target fields and owner "
                    "IRI in that single creator call and do not repeat the effect; otherwise use "
                    "the exposed target creator and relationship writer. A target class need not "
                    "be iteration-owned to be materializable through that compiled path."
                )

    if prompt_rules.get("require_top_entity_reuse"):
        lines.append(
            f"- Reuse the existing top-level entity IRI `{entity_uri}`. Do not replace it."
        )
    if prompt_rules.get("forbid_new_top_entity_creation"):
        lines.append("- Do not create a second top-level entity for this case.")
    if prompt_rules.get("require_required_links_before_export"):
        for spec in (ontology_contract or {}).get("required_links", []) or []:
            pred_iri = str((spec or {}).get("predicate_iri") or "").strip()
            min_count = int((spec or {}).get("min_count") or 0)
            if pred_iri and min_count > 0:
                pred_name = pred_iri.rsplit("/", 1)[-1]
                lines.append(
                    f"- Before export, satisfy the T-Box cardinality for `{pred_name}` "
                    f"(`{pred_iri}`): minimum {min_count} link(s) where the machine-readable contract applies."
                )
    if lines:
        lines.append("Config-derived graph integrity rules:")
        lines.append(
            f"- Use `{doi_hash}` as the document identifier/doi argument when relevant."
        )
        lines.append(f"- Keep all work scoped to entity label `{entity_label}`.")
        if _is_semantic_hint_content(hints_content):
            lines.extend(
                [
                    "- `ExtractedHints` is an independently audited semantic ledger, not a "
                    "graph-shaped payload. Derive the required individuals and relations from "
                    "its meaning; never demand refs, IDs, JSON fields, or pre-existing nodes.",
                    "- Derive every class boundary, occurrence identity rule, relation, reuse "
                    "rule, and any linked materialization exception exclusively from the "
                    "active T-Box comments, integrity annotations, and iteration KG prompt. "
                    "Do not introduce a domain-specific rule that is absent from those inputs.",
                    "- Materialize every explicit semantic fact in the ledger. Preserve exact "
                    "quantity lexemes and source order; do not invent graph facts merely to "
                    "make the graph appear complete.",
                ]
            )
        else:
            lines.append(
                "- Every explicit canonical field present in `ExtractedHints` must be preserved "
                "through the scoped KG workflow; do not terminate with only placeholder shell "
                "entities. Lexical evidence for object properties identified below is materialized "
                "by the deterministic post-export quantity processor, not as a creator argument."
            )
            lines.append(
                "- Treat each entity record/ref in `ExtractedHints` as one occurrence. After a "
                "successful creator call, bind that ref to the returned IRI and never invoke a "
                "creator again for that same ref during this attempt. If a later relationship call "
                "fails, correct its arguments or obtain the required reuse authorization; do not "
                "recreate either endpoint."
            )
        if lexical_object_fields:
            field_names = ", ".join(
                f"`{name}`" for name in sorted(lexical_object_fields)
            )
            lines.append(
                f"- The hint fields {field_names} are lexical evidence for T-Box object "
                "properties. Do not pass them as entity-creator keyword arguments and do not "
                "pass their lexical strings to relationship tools. Create the owning entity "
                "without those unsupported arguments; the deterministic quantity processor "
                "will create and link the range node from the preserved hint after export."
            )
        if ordering_locals:
            ordering_names = ", ".join(f"`{name}`" for name in ordering_locals)
            lines.append(
                f"- Ordered-member property fidelity is mandatory for the T-Box ordering "
                f"propert{'y' if len(ordering_locals) == 1 else 'ies'} {ordering_names}: "
                "materialize each property on the member with that exact order and never copy, "
                "inherit, or reuse a property value from a different ordered member."
            )
            lines.append(
                "- For every ordered-member creator, pass the exact positive integer order from "
                "the same hint directly in that creator call. The creator writes identity and "
                "order atomically. Do not infer order from labels, invent a separate order setter, "
                "or defer ordering to export or publisher repair."
            )
            lines.append(
                "- If two ordered members share the same class, still treat them as separate "
                "individuals with independent property values; each value must match the "
                "corresponding hinted member."
            )
            lines.append(
                "- When an ordered member hint omits an optional property, do not populate that "
                "property by copying it from another member unless the prompt or T-Box explicitly "
                "states an inheritance rule."
            )
        hints_text = _strip_code_fences(hints_content)
        ordering_pattern = (
            "|".join(re.escape(name) for name in ordering_locals)
            if ordering_locals
            else r"(?!x)x"
        )
        has_ordered_member_evidence = bool(
            re.search(r"<(?:step|member)\d+>", hints_text, flags=re.IGNORECASE)
            or re.search(
                rf"(?<![A-Za-z0-9_])(?:{ordering_pattern})(?![A-Za-z0-9_])",
                hints_text,
                flags=re.IGNORECASE,
            )
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
        return kg_prompt.rstrip() + "\n\n" + "\n".join(lines) + "\n"
    return kg_prompt


def _has_entity_persistence_artifact(
    *, doi_folder: str, entity_safe: str, entity_label: str
) -> bool:
    return bool(
        _entity_persistence_artifacts(
            doi_folder=doi_folder,
            entity_safe=entity_safe,
            entity_label=entity_label,
        )
    )


def _entity_persistence_artifacts(
    *, doi_folder: str, entity_safe: str, entity_label: str
) -> list[str]:
    mem_dir = os.path.join(doi_folder, "memory")
    candidates = [
        os.path.join(mem_dir, f"{entity_safe}.ttl"),
        os.path.join(mem_dir, f"{entity_safe.lower()}.ttl"),
        os.path.join(mem_dir, f"{entity_label}.ttl"),
    ]
    found = [path for path in candidates if os.path.exists(path)]
    exports_dir = os.path.join(doi_folder, "exports")
    if os.path.isdir(exports_dir):
        for name in os.listdir(exports_dir):
            if name.startswith(entity_safe) and name.endswith(".ttl"):
                found.append(os.path.join(exports_dir, name))
    return list(dict.fromkeys(found))


def _artifact_fingerprints(paths: list[str]) -> dict[str, tuple[int, int]]:
    """Capture mechanical file identity for per-attempt freshness checks."""
    fingerprints: dict[str, tuple[int, int]] = {}
    for path in paths:
        try:
            stat = os.stat(path)
        except OSError:
            continue
        fingerprints[path] = (stat.st_mtime_ns, stat.st_size)
    return fingerprints


def _publish_central_memory_after_semantic_commit(
    *,
    ttl_path: str,
    ontology_name: str,
    doi_hash: str,
    entity_scope: str,
) -> dict[str, Any]:
    """Publish reusable entities only after the pipeline accepts an attempt."""
    normalized_ontology = str(ontology_name or "").strip()
    if not normalized_ontology:
        return {"status": "skipped", "reason": "ontology_name is empty"}
    artifact_root = Path(
        os.environ.get("TWA_GENERATED_ARTIFACT_ROOT")
        or "ai_generated_contents_candidate"
    ).resolve()
    contract_path = (
        artifact_root
        / "scripts"
        / normalized_ontology
        / "_relationship_contract.json"
    )
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Cannot publish committed central memory without reuse contract: "
            f"{contract_path}: {exc}"
        ) from exc
    routed_classes: dict[str, list[str]] = {
        "central": [],
        "document": [],
        "scoped": [],
        "none": [],
    }
    for item in ((contract.get("reuse_policy") or {}).get("classes") or []):
        if not isinstance(item, dict):
            continue
        class_iri = str(item.get("class_iri") or "").strip()
        if not class_iri:
            continue
        reusable = item.get("reusable") is True
        destination = reuse_storage_scope(
            str(
                item.get("reuse_scope")
                or ("legacy_unspecified" if reusable else "never")
            ),
            reusable=reusable,
        )
        if destination in routed_classes:
            routed_classes[destination].append(class_iri)
    if not (routed_classes["central"] or routed_classes["document"]):
        return {"status": "skipped", "reason": "no reusable classes"}
    committed_graph = _parse_turtle_path(ttl_path)
    result: dict[str, Any] = {"status": "ok"}
    if routed_classes["central"]:
        result["central"] = publish_reusable_entities_to_central_memory(
            ontology_name=normalized_ontology,
            source_graph=committed_graph,
            reusable_class_iris=routed_classes["central"],
            excluded_class_iris=(
                routed_classes["document"]
                + routed_classes["scoped"]
                + routed_classes["none"]
            ),
            doi=doi_hash,
            top_level_entity_name=entity_scope,
        )
    if routed_classes["document"]:
        result["document"] = publish_reusable_entities_to_document_memory(
            ontology_name=normalized_ontology,
            source_graph=committed_graph,
            reusable_class_iris=routed_classes["document"],
            excluded_class_iris=(
                routed_classes["scoped"] + routed_classes["none"]
            ),
            doi=doi_hash,
            top_level_entity_name=entity_scope,
        )
    if "central" in result and "document" not in result:
        return result["central"]
    if "document" in result and "central" not in result:
        return result["document"]
    return result


def _snapshot_entity_retry_state(
    *,
    doi_folder: str,
    entity_safe: str,
    entity_label: str,
    ontology_name: str = "",
) -> dict[str, bytes | None]:
    """Capture canonical scoped files before a mutation attempt."""
    memory_dir = os.path.join(doi_folder, "memory")
    stems = {
        entity_safe,
        entity_safe.lower(),
        entity_label,
        _safe_name(entity_label),
    }
    paths = {
        os.path.join(memory_dir, f"{stem}{suffix}")
        for stem in stems
        for suffix in (".ttl", ".refs.json", ".checkpoint.json")
    }
    if ontology_name:
        central_dir = os.path.join(os.path.dirname(doi_folder), "central_memory")
        paths.update(
            {
                os.path.join(
                    central_dir, f"{ontology_name}_reusable_entities.ttl"
                ),
                os.path.join(
                    central_dir,
                    f"{ontology_name}_reusable_entities.provenance.json",
                ),
            }
        )
    exports_dir = os.path.join(doi_folder, "exports")
    export_patterns = {
        os.path.join(exports_dir, f"{stem}_*.ttl") for stem in stems
    }
    for pattern in export_patterns:
        paths.update(str(path) for path in Path(exports_dir).glob(Path(pattern).name))
    snapshot: dict[str, bytes | None] = {}
    for path in paths:
        try:
            snapshot[path] = Path(path).read_bytes()
        except FileNotFoundError:
            snapshot[path] = None
    for pattern in export_patterns:
        snapshot[f"__cleanup_glob__:{pattern}"] = None
    return snapshot


def _restore_entity_retry_state(snapshot: dict[str, bytes | None]) -> None:
    """Rollback failed-attempt scoped files so retries are transactional."""
    for marker in snapshot:
        if not marker.startswith("__cleanup_glob__:"):
            continue
        pattern = marker.removeprefix("__cleanup_glob__:")
        for path in Path(os.path.dirname(pattern)).glob(Path(pattern).name):
            path.unlink(missing_ok=True)
    for path, payload in snapshot.items():
        if path.startswith("__cleanup_glob__:"):
            continue
        native = _filesystem_path(path)
        if payload is None:
            try:
                os.remove(native)
            except FileNotFoundError:
                pass
            continue
        os.makedirs(_filesystem_path(os.path.dirname(path)), exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            dir=_filesystem_path(os.path.dirname(path)),
            suffix=".retry-rollback",
        )
        os.close(fd)
        try:
            with open(temporary, "wb") as handle:
                handle.write(payload)
            os.replace(temporary, native)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)


def _entity_ref_registry_path(doi_folder: str, entity_scope: str) -> str:
    return os.path.join(doi_folder, "memory", f"{entity_scope}.refs.json")


def _load_entity_ref_registry(
    doi_folder: str,
    entity_scope: str,
) -> dict[str, Any]:
    path = _entity_ref_registry_path(doi_folder, entity_scope)
    if not os.path.isfile(path):
        return {
            "schema_version": "pipeline-ref-registry.v1",
            "entity_scope": entity_scope,
            "refs": {},
        }
    try:
        with open(_filesystem_path(path), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": "pipeline-ref-registry.v1",
            "entity_scope": entity_scope,
            "refs": {},
        }
    refs = payload.get("refs") if isinstance(payload, dict) else {}
    return {
        "schema_version": "pipeline-ref-registry.v1",
        "entity_scope": entity_scope,
        "refs": refs if isinstance(refs, dict) else {},
    }


def _persist_entity_ref_registry(
    *,
    doi_folder: str,
    entity_scope: str,
    iteration: int,
    resolved_refs: dict[str, dict[str, Any]],
) -> str:
    """Commit deterministic hint-ref bindings after semantic completion."""
    payload = _load_entity_ref_registry(doi_folder, entity_scope)
    refs = dict(payload.get("refs") or {})
    for ref, raw_entry in sorted(resolved_refs.items()):
        if not ref or not isinstance(raw_entry, dict):
            continue
        iri = str(raw_entry.get("iri") or "").strip()
        if not iri:
            continue
        existing = refs.get(ref)
        existing_iri = (
            str(existing.get("iri") or "").strip()
            if isinstance(existing, dict)
            else ""
        )
        if existing_iri and existing_iri != iri:
            raise ValueError(
                f"Hint ref `{ref}` is already bound to {existing_iri}; "
                f"refusing conflicting binding to {iri}"
            )
        entry = {
            "iri": iri,
            "class": str(raw_entry.get("class") or "").strip(),
            "label": str(raw_entry.get("label") or "").strip(),
            "datatype_properties": (
                dict(raw_entry.get("datatype_properties") or {})
                if isinstance(raw_entry.get("datatype_properties"), dict)
                else {}
            ),
            "first_seen_iteration": (
                int(existing.get("first_seen_iteration"))
                if isinstance(existing, dict)
                and str(existing.get("first_seen_iteration") or "").isdigit()
                else int(iteration)
            ),
            "last_seen_iteration": int(iteration),
        }
        refs[ref] = entry
    payload["refs"] = refs
    path = _entity_ref_registry_path(doi_folder, entity_scope)
    _write_json_atomic(path, payload)
    return path


def _persisted_abox_entity_inventory(
    paths: list[str],
    *,
    ref_registry: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Return complete domain-independent identities from persisted A-Box TTL."""
    graph = Graph()
    for path in paths:
        try:
            graph += _parse_turtle_path(path)
        except Exception:
            continue
    refs_by_iri: dict[str, list[str]] = {}
    for ref, entry in (ref_registry or {}).get("refs", {}).items():
        if not isinstance(entry, dict):
            continue
        iri = str(entry.get("iri") or "").strip()
        if iri:
            refs_by_iri.setdefault(iri, []).append(str(ref))
    inventory: list[dict[str, Any]] = []
    for subject in sorted(
        {node for node in graph.subjects(RDF.type, None) if isinstance(node, URIRef)},
        key=str,
    ):
        type_iris = sorted(
            {
                str(type_iri)
                for type_iri in graph.objects(subject, RDF.type)
                if isinstance(type_iri, URIRef)
            }
        )
        labels = sorted({str(label) for label in graph.objects(subject, RDFS.label)})
        if not type_iris:
            continue
        datatype_values: dict[str, list[str]] = {}
        outgoing_relations: list[dict[str, str]] = []
        incoming_relations: list[dict[str, str]] = []
        for predicate, obj in graph.predicate_objects(subject):
            if predicate in {RDF.type, RDFS.label}:
                continue
            if isinstance(obj, Literal):
                datatype_values.setdefault(str(predicate), []).append(str(obj))
            elif isinstance(obj, URIRef):
                outgoing_relations.append(
                    {"property_iri": str(predicate), "object_iri": str(obj)}
                )
        for source, predicate in graph.subject_predicates(subject):
            if isinstance(source, URIRef):
                incoming_relations.append(
                    {"subject_iri": str(source), "property_iri": str(predicate)}
                )
        entry: dict[str, Any] = {
            "iri": str(subject),
            "types": type_iris,
            "labels": labels,
        }
        refs = sorted(set(refs_by_iri.get(str(subject), [])))
        if refs:
            entry["refs"] = refs
        if datatype_values:
            entry["datatype_values"] = {
                predicate: sorted(set(values))
                for predicate, values in sorted(datatype_values.items())
            }
        if outgoing_relations:
            entry["outgoing_relations"] = sorted(
                outgoing_relations,
                key=lambda item: (item["property_iri"], item["object_iri"]),
            )
        if incoming_relations:
            entry["incoming_relations"] = sorted(
                incoming_relations,
                key=lambda item: (item["property_iri"], item["subject_iri"]),
            )
        inventory.append(entry)
    return inventory


def _build_kg_recovery_prompt(
    *,
    base_prompt: str,
    entity_label: str,
    entity_uri: str,
    prior_attempt_trace: dict[str, Any],
) -> str:
    state_instruction = (
        "- Open or resume the scoped graph with `init_memory` before mutation. It is "
        "idempotent and accepts no reset/replace mode.\n"
        "- Do not call arbitrary-path loader, reset, replace, or clearing tools. The "
        "runtime resolves persisted state internally from DOI/entity scope.\n"
    )
    retry_state_instruction = (
        "- The failed attempt was rolled back. First rebuild the entire "
        "iteration-owned graph from this restored baseline and the current hints. "
        "Only after that rebuild, satisfy the supplementary presence obligations "
        "on the newly created occurrences. Do not patch or reuse instance IRIs "
        "from the failed attempt; those identifiers no longer exist.\n"
    )
    mutation_instruction = (
        f"- Create or reuse the scoped top-level entity `{entity_uri}` before "
        "creating or linking child entities.\n"
        "- Re-apply every source-grounded semantic fact from `ExtractedHints`, independent "
        "of its serialization shape, using only matching exposed tools.\n"
        "- For ordered members, create each hinted member with its order scalar and link "
        "each member individually to the scoped top entity.\n"
    )
    return (
        base_prompt.rstrip()
        + "\n\nRecovery instructions for this retry:\n"
        + f"- The previous attempt for `{entity_label}` failed persistence, tool-result, or graph-audit validation.\n"
        + "- Graph lifecycle mode: `open_or_resume` from the restored iteration baseline.\n"
        + state_instruction
        + "- Read `semantic_feedback` in the compact trace first. Treat listed findings as "
        "rebuild obligations for newly created occurrences, not in-place edits of the "
        "failed graph. Do not ignore, reinterpret, or merely describe a failed obligation.\n"
        + (
            "- Presence-coverage obligations from the previous attempt:\n"
            + str(
                ((prior_attempt_trace or {}).get("semantic_commit") or {}).get(
                    "presence_coverage_feedback"
                )
                or ""
            ).rstrip()
            + "\n"
            if ((prior_attempt_trace or {}).get("semantic_commit") or {}).get(
                "presence_coverage_feedback"
            )
            else ""
        )
        + retry_state_instruction
        + "- You must use MCP tools in this retry. Do not answer in prose until after the export tool succeeds.\n"
        + "- Follow the exposed MCP tool schemas exactly. Do not assume parameter names or positional arguments.\n"
        + "- Tool recipes in the presence feedback were generated from the exposed inventory "
        "at audit time. If a named tool is still in the live inventory, call it as specified. "
        "Do not invent a different tool or a later setter that the recipe says does not exist.\n"
        + "- Treat structured `ok=false` or status `rejected`, `error`, or `failed` as a failed mutation even when the transport reports success.\n"
        + "- `init_memory` is idempotent open-or-resume and has no destructive mode.\n"
        + mutation_instruction
        + "- Before export, confirm successful result envelopes and substantive scoped A-Box facts. Finally call `export_memory`; if it returns no A-Box TTL or no scoped top entity, do not claim success.\n"
        + "- Do not switch scope and do not create a second competing top-level entity.\n"
        + "\nStructured trace from the previous attempt:\n"
        + json.dumps(
            _compact_attempt_trace_for_recovery(prior_attempt_trace),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


def _can_accept_kg_after_audit_exhaustion(
    *,
    policy: dict[str, Any] | None,
    final_attempt: bool,
    current_attempt_artifacts: list[str],
    structured_tool_failure: bool,
    blocker_declared: bool,
    semantic_complete: bool,
    framework_integrity_report: dict[str, Any] | None,
    framework_ok: bool,
    semantic_graph_report: dict[str, Any] | None,
    semantic_graph_ok: bool,
) -> bool:
    """Fail open for persisted candidates rejected by exhausted LLM KG audits."""
    audit_rejected = bool(
        (framework_integrity_report is not None and not framework_ok)
        or (semantic_graph_report is not None and not semantic_graph_ok)
    )
    return bool(
        _semantic_audit_nonblocking(policy)
        and final_attempt
        and current_attempt_artifacts
        and not structured_tool_failure
        and not blocker_declared
        and not semantic_complete
        and audit_rejected
    )


def _semantic_audit_nonblocking(policy: dict[str, Any] | None) -> bool:
    """Whether exhausted LLM audits preserve a deterministically valid candidate."""
    audit_policy = (policy or {}).get("semantic_audit", {}) or {}
    return bool(audit_policy.get("nonblocking_after_semantic_exhaustion"))


def _compact_attempt_trace_for_recovery(
    trace: dict[str, Any],
    *,
    max_failures: int = 12,
) -> dict[str, Any]:
    """Keep retry guidance useful without replaying an entire ReAct transcript."""

    def compact_value(value: Any) -> Any:
        if isinstance(value, str):
            return value if len(value) <= 2000 else value[:2000] + "…"
        if isinstance(value, list):
            return [compact_value(item) for item in value[:20]]
        if isinstance(value, dict):
            return {
                str(key): compact_value(item)
                for key, item in value.items()
                if str(key) not in {"ttl", "content", "raw_content"}
            }
        return value

    attempted_tools: list[dict[str, Any]] = []
    for call in trace.get("planned_tool_calls") or []:
        if not isinstance(call, dict):
            continue
        args = call.get("args") or call.get("arguments") or {}
        attempted_tools.append(
            {
                "name": str(call.get("name") or ""),
                "argument_names": sorted(str(key) for key in args)
                if isinstance(args, dict)
                else [],
            }
        )

    failures: list[dict[str, Any]] = []
    for output in trace.get("tool_outputs") or []:
        if not isinstance(output, dict):
            continue
        payload = output.get("structured_content")
        if not isinstance(payload, dict):
            try:
                payload = json.loads(str(output.get("content") or ""))
            except (TypeError, json.JSONDecodeError):
                payload = {}
        status = str(payload.get("status") or output.get("status") or "").casefold()
        if payload.get("ok") is not False and status not in {
            "rejected",
            "error",
            "failed",
        }:
            continue
        failures.append(
            {
                "name": str(output.get("name") or ""),
                "status": status or "error",
                "diagnostic": compact_value(payload),
            }
        )

    semantic_commit = trace.get("semantic_commit") or {}
    semantic_findings: list[dict[str, Any]] = []
    _iri_keys = {
        "matched_iri",
        "owner_iri",
        "iri",
        "subject_iri",
        "object_iri",
        "entity_iri",
    }
    for raw in semantic_commit.get("hint_fidelity_messages") or []:
        item: Any = raw
        if isinstance(raw, str):
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                item = {"message": raw}
        if not isinstance(item, dict):
            item = {"message": str(item)}
        evidence = item.get("evidence")
        if isinstance(evidence, dict):
            evidence = {
                key: value
                for key, value in evidence.items()
                if str(key) not in _iri_keys
            }
        semantic_findings.append(
            {
                "check_id": str(item.get("check_id") or ""),
                "subject_key": str(item.get("subject_key") or ""),
                "message": compact_value(item.get("message") or ""),
                "evidence": compact_value(evidence) if evidence else {},
            }
        )

    return {
        "artifact_found": bool(trace.get("artifact_found")),
        "structured_tool_failure": bool(trace.get("structured_tool_failure")),
        "unresolved_obligations": compact_value(
            list(trace.get("unresolved_obligations") or [])[-max_failures:]
        ),
        "semantic_feedback": {
            "validation_policy": str(
                semantic_commit.get("validation_policy") or ""
            ),
            "accepted": bool(semantic_commit.get("hint_fidelity_ok")),
            "findings": semantic_findings,
            "presence_coverage_feedback": str(
                semantic_commit.get("presence_coverage_feedback") or ""
            ),
        },
        "attempted_tools": attempted_tools[-30:],
        "failures": failures[-max_failures:],
    }


def _canonical_tool_args_sha256(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    canonical = json.dumps(
        args,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _structured_tool_payload(output: dict[str, Any]) -> dict[str, Any]:
    payload = output.get("structured_content")
    if isinstance(payload, dict):
        return payload
    try:
        parsed = json.loads(str(output.get("content") or ""))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _unresolved_facet_obligations(
    activity: dict[str, Any],
) -> list[dict[str, Any]]:
    planned_by_id = {
        str(call.get("id") or ""): call
        for call in activity.get("planned_tool_calls") or []
        if isinstance(call, dict) and str(call.get("id") or "")
    }
    pending: dict[tuple[str, str], dict[str, Any]] = {}
    for output in activity.get("tool_outputs") or []:
        if not isinstance(output, dict):
            continue
        payload = _structured_tool_payload(output)
        status = str(
            payload.get("status") or output.get("status") or ""
        ).casefold()
        if status in {"skip", "skipped"} and payload.get("policy_valid") is True:
            resolved_id = str(payload.get("obligation_id") or "").strip()
            if resolved_id:
                pending = {
                    key: item
                    for key, item in pending.items()
                    if item.get("identity") != resolved_id
                }
            continue
        if status != "ok" or payload.get("ok") is False:
            continue
        fingerprint = str(
            payload.get("semantic_fingerprint")
            or output.get("semantic_fingerprint")
            or ""
        ).strip()
        if not fingerprint:
            continue
        call_id = str(output.get("tool_call_id") or "")
        planned = planned_by_id.get(call_id) or {}
        args = planned.get("args") or planned.get("arguments") or {}
        supplied = {str(key) for key in args} if isinstance(args, dict) else set()
        warnings = [
            item
            for item in payload.get("facet_warnings") or []
            if isinstance(item, dict) and str(item.get("facet") or "").strip()
        ]
        warned_facets = {str(item.get("facet")).strip() for item in warnings}
        for key in list(pending):
            prior_fingerprint, prior_facet = key
            if (
                prior_fingerprint == fingerprint
                and prior_facet in supplied
                and prior_facet not in warned_facets
            ):
                pending.pop(key, None)
        for warning in warnings:
            facet = str(warning.get("facet") or "").strip()
            obligation_id = str(warning.get("obligation_id") or "").strip()
            pending[(fingerprint, facet)] = {
                "identity": obligation_id or f"{fingerprint}:facet:{facet}",
                "identity_tokens": [
                    value
                    for value in (obligation_id, fingerprint, f"facet:{facet}")
                    if value
                ],
                "legacy_identity": "",
                "tool_name": str(output.get("name") or ""),
                "status": "error",
                "code": str(
                    warning.get("code") or "QUANTITY_FACET_OMITTED"
                ),
                "retryable": True,
                "skippable": bool(warning.get("skippable")),
                "tool_call_id": call_id,
                "args_sha256": _canonical_tool_args_sha256(args),
                "facet": facet,
                "message": str(warning.get("message") or ""),
            }
    return list(pending.values())


def _unresolved_structured_obligations(
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Track rejected structured calls until the same obligation is repaired."""
    activity = (metadata or {}).get("tool_activity") or {}
    planned_calls = [
        call
        for call in activity.get("planned_tool_calls") or []
        if isinstance(call, dict)
    ]
    planned_by_id = {
        str(call.get("id") or ""): call
        for call in planned_calls
        if str(call.get("id") or "")
    }
    planned_identities_by_name: dict[str, set[str]] = {}
    for call in planned_calls:
        name = str(call.get("name") or "").strip()
        args_hash = _canonical_tool_args_sha256(
            call.get("args")
            if isinstance(call.get("args"), dict)
            else call.get("arguments")
        )
        if name and args_hash:
            planned_identities_by_name.setdefault(name, set()).add(args_hash)

    pending: list[dict[str, Any]] = []
    for index, output in enumerate(activity.get("tool_outputs") or []):
        if not isinstance(output, dict):
            continue
        payload = _structured_tool_payload(output)
        status = str(payload.get("status") or output.get("status") or "").casefold()
        failed = (
            str(output.get("status") or "").casefold() == "error"
            or payload.get("ok") is False
            or status in {"rejected", "error", "failed"}
        )
        policy_valid = payload.get("policy_valid") is True or str(
            payload.get("policy_valid") or ""
        ).casefold() == "true"
        repaired = status == "ok" or (
            status in {"skip", "skipped"} and policy_valid
        )
        if not failed and not repaired:
            continue

        explicit_tokens: set[str] = set()
        for source in (output, payload):
            for key in ("semantic_fingerprint", "fingerprint", "obligation_id"):
                value = source.get(key)
                if value is not None and str(value).strip():
                    explicit_tokens.add(str(value).strip())

        name = str(output.get("name") or "").strip()
        args_hash = str(
            payload.get("args_sha256") or output.get("args_sha256") or ""
        ).strip()
        planned = planned_by_id.get(str(output.get("tool_call_id") or ""))
        if planned is not None:
            name = name or str(planned.get("name") or "").strip()
            args_hash = args_hash or _canonical_tool_args_sha256(
                planned.get("args")
                if isinstance(planned.get("args"), dict)
                else planned.get("arguments")
            )
        if not args_hash:
            output_args = output.get("args") or output.get("arguments")
            args_hash = _canonical_tool_args_sha256(output_args)
        if not args_hash and name:
            candidates = planned_identities_by_name.get(name) or set()
            if len(candidates) == 1:
                args_hash = next(iter(candidates))

        legacy_identity = f"{name}:{args_hash}" if name and args_hash else ""

        def same_obligation(item: dict[str, Any]) -> bool:
            prior_tokens = set(item.get("identity_tokens") or [])
            if explicit_tokens or prior_tokens:
                return bool(explicit_tokens and prior_tokens & explicit_tokens)
            return bool(
                legacy_identity
                and item.get("legacy_identity") == legacy_identity
            )

        if repaired:
            pending = [item for item in pending if not same_obligation(item)]
            continue

        if any(same_obligation(item) for item in pending):
            continue
        identity = (
            sorted(explicit_tokens)[0]
            if explicit_tokens
            else legacy_identity
            if legacy_identity
            else f"unresolved-output-{index + 1}"
        )
        pending.append(
            {
                "identity": identity,
                "identity_tokens": sorted(explicit_tokens),
                "legacy_identity": legacy_identity,
                "tool_name": name,
                "status": status or "error",
                "code": str(payload.get("code") or ""),
                "retryable": bool(payload.get("retryable")),
                "skippable": bool(payload.get("skippable")),
                "tool_call_id": str(output.get("tool_call_id") or ""),
                "args_sha256": args_hash,
            }
        )

    return pending + _unresolved_facet_obligations(activity)


def _tool_trace_has_structured_failure(metadata: dict[str, Any]) -> bool:
    """Return whether any structured rejection remains unresolved."""
    return bool(_unresolved_structured_obligations(metadata))


def _resolved_argument_owner_repairs(
    activity: dict[str, Any],
) -> list[dict[str, Any]]:
    outputs = [
        item
        for item in activity.get("tool_outputs") or []
        if isinstance(item, dict)
    ]
    repairs: list[dict[str, Any]] = []
    for index, output in enumerate(outputs):
        payload = _structured_tool_payload(output)
        if str(payload.get("code") or "") != "ARGUMENT_OWNER_MISMATCH":
            continue
        fingerprint = str(
            payload.get("semantic_fingerprint")
            or output.get("semantic_fingerprint")
            or ""
        )
        if not fingerprint:
            continue
        repaired_by: dict[str, Any] | None = None
        for later in outputs[index + 1 :]:
            later_payload = _structured_tool_payload(later)
            later_fingerprint = str(
                later_payload.get("semantic_fingerprint")
                or later.get("semantic_fingerprint")
                or ""
            )
            later_status = str(
                later_payload.get("status") or later.get("status") or ""
            ).casefold()
            if (
                later_fingerprint == fingerprint
                and later_payload.get("ok") is not False
                and later_status
                not in {"error", "failed", "failure", "rejected"}
            ):
                repaired_by = later
                break
        if repaired_by is None:
            continue
        repairs.append(
            {
                "semantic_fingerprint": fingerprint,
                "rejected_tool_call_id": str(
                    output.get("tool_call_id") or ""
                ),
                "repaired_tool_call_id": str(
                    repaired_by.get("tool_call_id") or ""
                ),
                "tool_name": str(output.get("name") or ""),
                "invalid_arguments": list(
                    payload.get("invalid_arguments") or []
                ),
                "argument_owners": dict(payload.get("argument_owners") or {}),
                "status": "repaired",
            }
        )
    return repairs


def _attempt_trace(metadata: dict[str, Any], *, artifact_found: bool) -> dict[str, Any]:
    activity = (metadata or {}).get("tool_activity") or {}
    unresolved = _unresolved_structured_obligations(metadata)
    firewall_warnings = [
        dict(warning)
        for call in activity.get("planned_tool_calls") or []
        if isinstance(call, dict)
        for warning in [call.get("argument_firewall_warning")]
        if isinstance(warning, dict)
    ]
    owner_repairs = _resolved_argument_owner_repairs(activity)
    return {
        "artifact_found": artifact_found,
        "planned_tool_calls": list(activity.get("planned_tool_calls") or []),
        "tool_outputs": list(activity.get("tool_outputs") or []),
        "structured_tool_failure": bool(unresolved),
        "unresolved_obligations": unresolved,
        "argument_firewall_warnings": firewall_warnings,
        "argument_owner_repairs": owner_repairs,
        "usage": (metadata or {}).get("aggregated_usage") or {},
    }


def _successful_mutation_tools(metadata: dict[str, Any]) -> list[str]:
    """Return successful graph mutation tool names, excluding lifecycle exports."""
    names: list[str] = []
    for output in ((metadata or {}).get("tool_activity") or {}).get(
        "tool_outputs"
    ) or []:
        name = str(output.get("name") or "").strip()
        if not (name.startswith("create_") or name.startswith("add_")):
            continue
        payload = output.get("structured_content")
        if not isinstance(payload, dict):
            try:
                payload = json.loads(str(output.get("content") or ""))
            except (TypeError, json.JSONDecodeError):
                payload = {}
        status = str((payload or {}).get("status") or output.get("status") or "").casefold()
        if (payload or {}).get("ok") is False or status in {
            "rejected",
            "error",
            "failed",
            "failure",
        }:
            continue
        names.append(name)
    return names


def _parse_iteration_owned_surface(prompt: str) -> tuple[set[str], set[str]]:
    """Read the fail-closed canonical ownership lines from a generated KG prompt."""
    surfaces: list[set[str]] = []
    for key in ("classes", "object_properties"):
        match = re.search(
            rf"(?im)^\s*(?:[-*]\s*)?Iteration-owned {key}:\s*\[([^\]\n]*)\]\s*$",
            str(prompt or ""),
        )
        surfaces.append(
            {
                value.strip().strip("`'\"")
                for value in (match.group(1).split(",") if match else [])
                if value.strip().strip("`'\"")
            }
        )
    return surfaces[0], surfaces[1]


def _compiled_iteration_owned_surface(
    iteration_spec: Optional[dict],
) -> tuple[set[str], set[str]]:
    """Return ownership from a compiled iteration, independent of prompt prose."""
    if not isinstance(iteration_spec, dict):
        return set(), set()
    semantic_scope = iteration_spec.get("semantic_scope") or {}
    responsibilities = iteration_spec.get("responsibilities") or {}

    def locals_from_scope(key: str) -> set[str]:
        values = semantic_scope.get(key) or []
        return {
            str(item.get("local") or "").strip()
            for item in values
            if isinstance(item, dict) and str(item.get("local") or "").strip()
        }

    classes = locals_from_scope("classes") or {
        str(value).strip()
        for value in responsibilities.get("classes") or []
        if str(value).strip()
    }
    properties = locals_from_scope("object_properties") or {
        str(value).strip()
        for value in responsibilities.get("object_properties") or []
        if str(value).strip()
    }
    classes.update(
        str(value).strip()
        for value in iteration_spec.get("linked_materialization_classes") or []
        if str(value).strip()
    )
    return classes, properties


def _validate_hint_relation_contract(
    *,
    hints_content: str,
    ontology_contract: Optional[dict],
    prior_ref_registry: Optional[dict[str, Any]] = None,
    iteration: int | None = None,
) -> list[dict[str, Any]]:
    """Detect impossible hinted relation endpoints before graph mutation."""
    payload = _decode_structured_hint_prefix(hints_content)
    entities = payload.get("entities")
    relations = payload.get("relations")
    if not isinstance(relations, list):
        return []

    ref_classes: dict[str, str] = {}
    for ref, entry in (prior_ref_registry or {}).get("refs", {}).items():
        if isinstance(entry, dict):
            class_local = _local_name(
                str(entry.get("class") or entry.get("class_iri") or "")
            )
            if class_local:
                ref_classes[str(ref)] = class_local
    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            ref = str(entity.get("ref") or "").strip()
            class_local = _local_name(str(entity.get("class") or ""))
            if ref and class_local:
                ref_classes[ref] = class_local

    object_contracts = {
        _local_name(str(item.get("property_iri") or "")): item
        for item in (ontology_contract or {}).get("object_properties", []) or []
        if str(item.get("property_iri") or "").strip()
    }
    superclass_locals = {
        _local_name(str(item.get("class_iri") or "")): {
            _local_name(str(value))
            for value in item.get("superclass_iris") or []
            if str(value).strip()
        }
        for item in (ontology_contract or {}).get("subclass_closure", []) or []
        if str(item.get("class_iri") or "").strip()
    }
    violations: list[dict[str, Any]] = []
    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            continue
        property_local = _local_name(str(relation.get("property") or ""))
        contract = object_contracts.get(property_local)
        if not contract:
            continue
        subject_ref = str(relation.get("subject_ref") or "").strip()
        object_ref = str(relation.get("object_ref") or "").strip()
        subject_class = ref_classes.get(subject_ref, "")
        object_class = ref_classes.get(object_ref, "")
        expected_domains = sorted(
            {
                _local_name(str(value))
                for value in contract.get("domain_iris") or []
                if str(value).strip()
            }
        )
        expected_ranges = sorted(
            {
                _local_name(str(value))
                for value in contract.get("range_iris") or []
                if str(value).strip()
            }
        )
        for role, actual_class, expected_classes in (
            ("subject", subject_class, expected_domains),
            ("object", object_class, expected_ranges),
        ):
            compatible_classes = superclass_locals.get(
                actual_class,
                {actual_class},
            )
            if (
                not actual_class
                or actual_class in expected_classes
                or compatible_classes.intersection(expected_classes)
            ):
                continue
            violations.append(
                {
                    "schema_version": "kg-hint-contract-violation.v1",
                    "code": (
                        "HINT_RELATION_DOMAIN_MISMATCH"
                        if role == "subject"
                        else "HINT_RELATION_RANGE_MISMATCH"
                    ),
                    "iteration": iteration,
                    "relation_index": index,
                    "property": property_local,
                    "subject_ref": subject_ref,
                    "object_ref": object_ref,
                    "endpoint_role": role,
                    "actual_class": actual_class,
                    "expected_classes": expected_classes,
                    "repair_action": (
                        "Revise the extraction hint. Do not force KG building to "
                        "materialize a relation whose endpoint class violates the "
                        "immutable T-Box contract."
                    ),
                }
            )
    return violations


def _validate_hint_fidelity(
    *,
    ttl_path: str,
    hints_content: str,
    ontology_contract: Optional[dict],
    owned_classes: set[str],
    owned_object_properties: set[str],
    prior_ref_registry: Optional[dict[str, Any]] = None,
    resolved_refs_out: Optional[dict[str, dict[str, Any]]] = None,
) -> tuple[bool, list[str], int]:
    """Validate ref-entity-relations hints against the scoped graph."""
    payload = _decode_structured_hint_prefix(hints_content)
    entities = payload.get("entities")
    relations = payload.get("relations")
    if not isinstance(entities, list) and not isinstance(relations, list):
        return True, [], 0
    entities = entities if isinstance(entities, list) else []
    relations = relations if isinstance(relations, list) else []
    try:
        graph = _parse_turtle_path(ttl_path)
    except Exception as exc:
        return False, [f"Failed to parse TTL for hint fidelity: {exc}"], 1

    contract = ontology_contract or {}
    class_iris = {
        _local_name(str(item.get("class_iri") or "")): str(
            item.get("class_iri") or ""
        ).strip()
        for item in contract.get("classes", []) or []
        if str(item.get("class_iri") or "").strip()
    }
    property_iris = {
        _local_name(str(item.get("property_iri") or "")): str(
            item.get("property_iri") or ""
        ).strip()
        for collection in ("datatype_properties", "object_properties")
        for item in contract.get(collection, []) or []
        if str(item.get("property_iri") or "").strip()
    }
    object_property_locals = {
        _local_name(str(item.get("property_iri") or ""))
        for item in contract.get("object_properties", []) or []
        if str(item.get("property_iri") or "").strip()
    }
    ref_nodes: dict[str, set[URIRef]] = {}
    for ref, entry in (prior_ref_registry or {}).get("refs", {}).items():
        if not isinstance(entry, dict):
            continue
        iri = str(entry.get("iri") or "").strip()
        node = URIRef(iri) if iri.startswith(("http://", "https://", "urn:")) else None
        if node is not None and (
            any(graph.triples((node, None, None)))
            or any(graph.triples((None, None, node)))
        ):
            ref_nodes[str(ref)] = {node}
    errors: list[str] = []
    expectations = 0

    for raw_entity in entities:
        if not isinstance(raw_entity, dict):
            continue
        ref = str(raw_entity.get("ref") or "").strip()
        class_local = _local_name(str(raw_entity.get("class") or "").strip())
        label = str(raw_entity.get("label") or "").strip()
        iri = str(raw_entity.get("iri") or "").strip()
        is_owned = not owned_classes or class_local in owned_classes
        is_referenced = any(
            isinstance(relation, dict)
            and ref
            and (
                not owned_object_properties
                or _local_name(
                    str(relation.get("property") or "").strip()
                )
                in owned_object_properties
            )
            and ref
            in {
                str(relation.get("subject_ref") or "").strip(),
                str(relation.get("object_ref") or "").strip(),
            }
            for relation in relations
        )
        if not is_owned and not is_referenced:
            continue
        expectations += 1
        class_iri = class_iris.get(class_local, "")
        candidates: set[URIRef] = set()
        if iri.startswith(("http://", "https://", "urn:")):
            candidates.add(URIRef(iri))
        elif ref.startswith(("http://", "https://", "urn:")):
            candidates.add(URIRef(ref))
        elif class_iri:
            candidates.update(
                node
                for node in graph.subjects(RDF.type, URIRef(class_iri))
                if isinstance(node, URIRef)
            )
        if class_iri:
            candidates = {
                node
                for node in candidates
                if (node, RDF.type, URIRef(class_iri)) in graph
            }
        if label:
            candidates = {
                node
                for node in candidates
                if any(str(value) == label for value in graph.objects(node, RDFS.label))
            }
        datatype_properties = raw_entity.get("datatype_properties")
        if isinstance(datatype_properties, dict):
            for raw_property, raw_value in datatype_properties.items():
                property_local = _local_name(str(raw_property))
                # Extraction prompts preserve lexical quantity evidence under
                # datatype_properties even when the T-Box predicate is an
                # object property. It guides quantity-node materialization but
                # is not a literal assertion on the source entity.
                if property_local in object_property_locals:
                    continue
                property_iri = property_iris.get(property_local, "")
                if not property_iri or isinstance(raw_value, (dict, list)):
                    continue
                def literal_matches(value: Literal) -> bool:
                    actual = value.toPython()
                    if isinstance(raw_value, bool):
                        return isinstance(actual, bool) and actual is raw_value
                    if (
                        isinstance(raw_value, (int, float))
                        and not isinstance(raw_value, bool)
                    ):
                        return (
                            isinstance(actual, (int, float))
                            and not isinstance(actual, bool)
                            and actual == raw_value
                        )
                    return str(value).strip() == str(raw_value).strip()

                candidates = {
                    node
                    for node in candidates
                    if any(
                        isinstance(value, Literal) and literal_matches(value)
                        for value in graph.objects(node, URIRef(property_iri))
                    )
                }
        if len(candidates) > 1:
            errors.append(
                "Ambiguous hinted entity "
                f"ref={ref or '<none>'}, class={class_local or '<none>'}, "
                f"label={label or '<none>'}, candidates={sorted(map(str, candidates))}"
            )
            candidates = set()
        elif not candidates:
            errors.append(
                "Missing hinted entity "
                f"ref={ref or '<none>'}, class={class_local or '<none>'}, "
                f"label={label or '<none>'}"
            )
        if ref:
            ref_nodes[ref] = candidates
            if len(candidates) == 1 and resolved_refs_out is not None:
                resolved_refs_out[ref] = {
                    "iri": str(next(iter(candidates))),
                    "class": class_local,
                    "label": label,
                    "datatype_properties": (
                        dict(datatype_properties)
                        if isinstance(datatype_properties, dict)
                        else {}
                    ),
                }

    for raw_relation in relations:
        if not isinstance(raw_relation, dict):
            continue
        property_local = _local_name(str(raw_relation.get("property") or "").strip())
        if owned_object_properties and property_local not in owned_object_properties:
            continue
        expectations += 1
        property_iri = property_iris.get(property_local, "")
        subject_ref = str(raw_relation.get("subject_ref") or "").strip()
        object_ref = str(raw_relation.get("object_ref") or "").strip()
        subjects = ref_nodes.get(subject_ref, set())
        objects = ref_nodes.get(object_ref, set())
        if (
            not subjects
            and subject_ref.startswith(("http://", "https://", "urn:"))
            and any(graph.triples((URIRef(subject_ref), None, None)))
        ):
            subjects = {URIRef(subject_ref)}
        if (
            not objects
            and object_ref.startswith(("http://", "https://", "urn:"))
            and any(graph.triples((URIRef(object_ref), None, None)))
        ):
            objects = {URIRef(object_ref)}
        if (
            not property_iri
            or not subjects
            or not objects
            or not any(
                (subject, URIRef(property_iri), object_) in graph
                for subject in subjects
                for object_ in objects
            )
        ):
            errors.append(
                "Missing hinted relation "
                f"{subject_ref or '<none>'} -{property_local or '<none>'}-> "
                f"{object_ref or '<none>'}"
            )
    return not errors, errors, expectations


def _filesystem_path(path: str) -> str:
    """Return a Windows extended path while preserving normal paths in reports."""
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _parse_turtle_path(path: str) -> Graph:
    """Parse Turtle through a Windows-long-path-safe binary read."""
    with open(_filesystem_path(path), "rb") as handle:
        payload = handle.read()
    graph = Graph()
    graph.parse(data=payload.decode("utf-8"), format="turtle")
    return graph


def _write_json_atomic(path: str, payload: dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    native_directory = _filesystem_path(directory)
    native_path = _filesystem_path(path)
    os.makedirs(native_directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=native_directory, suffix=".json.tmp")
    os.close(fd)
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        replace_with_retry(temp_path, native_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _termination_reason(exc: BaseException) -> str:
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    text = f"{type(exc).__name__}: {exc}".casefold()
    if "recursion" in text:
        return "recursion_limit"
    if "circuit" in text or "breaker" in text:
        return "circuit_breaker"
    return "exception"


def _partial_trace_from_exception(exc: BaseException) -> dict[str, Any]:
    """Best-effort extraction for runtimes that attach interrupted ReAct state."""
    partial: dict[str, Any] = {}
    for attr in ("partial_trace", "trace", "metadata"):
        value = getattr(exc, attr, None)
        if isinstance(value, dict):
            partial.update(value)
    activity = getattr(exc, "tool_activity", None)
    if isinstance(activity, dict):
        partial["tool_activity"] = activity
    state = getattr(exc, "state", None)
    if isinstance(state, dict) and isinstance(state.get("messages"), list):
        try:
            from models.BaseAgent import _summarize_react_tool_activity

            partial["tool_activity"] = _summarize_react_tool_activity(
                state["messages"]
            )
        except Exception:
            pass
    if isinstance(partial.get("tool_activity"), dict):
        return _attempt_trace(partial, artifact_found=False)
    return partial


def _merge_attempt_termination_trace(
    path: str,
    *,
    base: dict[str, Any],
    exc: BaseException,
    leaf_exceptions: list[dict[str, Any]] | None = None,
) -> None:
    """Persist termination evidence without replacing a trace already written."""
    existing: dict[str, Any] = {}
    try:
        with open(_filesystem_path(path), "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
            if isinstance(loaded, dict):
                existing = loaded
    except (OSError, json.JSONDecodeError):
        pass

    merged = dict(existing)
    partial = _partial_trace_from_exception(exc)
    for key, value in {**base, **partial}.items():
        if key not in merged or merged[key] in (None, "", [], {}):
            merged[key] = value
    merged.setdefault("termination_reason", _termination_reason(exc))
    merged.setdefault(
        "exception",
        {
            "type": type(exc).__name__,
            "message": str(exc),
            "leaves": list(leaf_exceptions or []),
        },
    )
    unresolved = list(merged.get("unresolved_obligations") or [])
    if unresolved:
        merged["structured_tool_failure"] = True
        semantic_commit = dict(merged.get("semantic_commit") or {})
        semantic_commit["complete"] = False
        semantic_commit["unresolved_obligations"] = unresolved
        merged["semantic_commit"] = semantic_commit
    _write_json_atomic(path, merged)


def bind_kg_runtime_context(
    kg_prompt: str,
    *,
    doi_hash: str,
    entity_label: str,
    entity_uri: str,
    hints_content: str,
    iter_num: int,
    identity_dossier: dict | None = None,
) -> tuple[str, list[str]]:
    """Bind the complete pipeline-owned entity KG runtime envelope."""
    declared_doi = "{doi}" in kg_prompt or "{hash}" in kg_prompt
    declared_label = "{entity_label}" in kg_prompt
    declared_uri = "{entity_uri}" in kg_prompt
    declared_dossier = "{entity_identity_dossier}" in kg_prompt
    dossier_text = json.dumps(
        identity_dossier or {},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    prompt = kg_prompt.replace("{doi}", doi_hash).replace("{hash}", doi_hash)
    prompt = prompt.replace("{entity_label}", entity_label)
    prompt = prompt.replace("{entity_uri}", entity_uri)
    prompt = prompt.replace("{entity_identity_dossier}", dossier_text)
    structured_hints = (
        "These are extracted hints for this iteration. Treat them as the primary source for KG building.\n"
        "Do not downgrade an explicit canonical field in these hints into a weaker fallback field.\n\n"
        "ExtractedHints:\n<<<\n"
        f"{hints_content}\n"
        ">>>\n"
    )
    warnings: list[str] = []
    declared_hints = False
    if "{iteration_hints}" in prompt:
        prompt = prompt.replace("{iteration_hints}", structured_hints)
        declared_hints = True
    elif iter_num >= 2 and "{paper_content}" in prompt:
        warnings.append(
            "Legacy KG Iteration 2+ prompt used {paper_content}; bound it to "
            "ExtractedHints rather than raw paper content."
        )
        prompt = prompt.replace("{paper_content}", structured_hints)
        declared_hints = True
    elif iter_num >= 2:
        warnings.append(
            "KG Iteration 2+ prompt omitted {iteration_hints}; appended the "
            "pipeline-owned ExtractedHints boundary."
        )

    additions: list[str] = []
    missing_identity: list[str] = []
    if not declared_doi:
        missing_identity.append(f"Document DOI/hash: {doi_hash}")
    if not declared_label:
        missing_identity.append(f"Current entity label: {entity_label}")
    if not declared_uri:
        missing_identity.append(
            f"Current entity exact URI (reuse this IRI; do not mint a replacement): {entity_uri}"
        )
    if missing_identity:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ENTITY RUNTIME CONTEXT: BEGIN ----",
                *missing_identity,
                "---- PIPELINE-INJECTED ENTITY RUNTIME CONTEXT: END ----",
            ]
        )
    if identity_dossier and not declared_dossier:
        additions.extend(
            [
                "---- PIPELINE-INJECTED ENTITY IDENTITY DOSSIER: BEGIN ----",
                "This dossier is the authoritative identity scope for the current entity.",
                "Materialize only facts for this exact scope and reuse its exact URI.",
                "Use only explicit dossier fields and facts; do not infer missing identity facts.",
                dossier_text,
                "---- PIPELINE-INJECTED ENTITY IDENTITY DOSSIER: END ----",
            ]
        )
    if iter_num >= 2 and not declared_hints:
        additions.extend(
            [
                "---- PIPELINE-INJECTED EXTRACTED HINTS: BEGIN ----",
                structured_hints.rstrip(),
                "---- PIPELINE-INJECTED EXTRACTED HINTS: END ----",
            ]
        )
    if additions:
        prompt = prompt.rstrip() + "\n\n" + "\n".join(additions) + "\n"
    return prompt, warnings


def _persist_structured_turtle_result(
    metadata: dict[str, Any], *, target_path: str
) -> tuple[bool, str]:
    """
    Persist a Turtle payload returned by a successful structured MCP tool result.

    This is a transport adapter only: it parses the declared Turtle payload and
    writes it atomically. Domain and entity-scope validation remains downstream.
    """
    activity = (metadata or {}).get("tool_activity") or {}
    turtle_payloads: list[str] = []
    for output in activity.get("tool_outputs") or []:
        if not isinstance(output, dict):
            continue
        if str(output.get("status") or "").casefold() == "error":
            continue
        payload = output.get("structured_content")
        if not isinstance(payload, dict) or payload.get("ok") is False:
            continue
        ttl = payload.get("ttl")
        if isinstance(ttl, str) and ttl.strip():
            turtle_payloads.append(ttl)

    if not turtle_payloads:
        return False, "No Turtle payload was present in successful structured tool results"

    ttl = turtle_payloads[-1]
    try:
        graph = Graph()
        graph.parse(data=ttl, format="turtle")
    except Exception as exc:
        return False, f"Structured Turtle payload could not be parsed: {exc}"
    if not graph:
        return False, "Structured Turtle payload parsed to an empty graph"

    directory = os.path.dirname(target_path)
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=directory, suffix=".ttl.tmp")
    os.close(fd)
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write(ttl)
        os.replace(temp_path, target_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return True, ""


def _validate_entity_ttl_structure(
    *,
    ttl_path: str,
    entity_uri: str,
    entity_label: str,
    ontology_contract: dict,
    known_top_entity_uris: Optional[set[str]] = None,
) -> tuple[bool, list[str]]:
    """Validate a published entity graph against its ontology-derived contract."""
    messages: list[str] = []
    if not ontology_contract or not ontology_contract.get("resolved_ttl_file"):
        return False, ["Ontology publish contract is unavailable"]
    if not ttl_path or not os.path.exists(ttl_path):
        return False, [f"TTL not found for validation: {ttl_path}"]

    try:
        g = _parse_turtle_path(ttl_path)
    except Exception as e:
        return False, [f"Failed to parse TTL: {e}"]

    entity_ref = URIRef(str(entity_uri or "").strip())
    if not entity_uri or not any(g.triples((entity_ref, None, None))):
        messages.append(f"Missing entity subject in TTL: {entity_uri}")

    closure = {
        str(item.get("class_iri") or ""): set(
            item.get("superclass_iris") or []
        )
        for item in ontology_contract.get("subclass_closure") or []
    }
    property_contracts = {
        str(item.get("property_iri") or ""): item
        for item in ontology_contract.get("object_properties") or []
    }

    def node_matches_class(node: URIRef, expected_class: str) -> bool:
        asserted_types = {
            str(value)
            for value in g.objects(node, RDF.type)
            if isinstance(value, URIRef)
        }
        return any(
            expected_class in closure.get(actual, {actual})
            for actual in asserted_types
        )

    for subject, predicate, obj in g:
        if not isinstance(obj, URIRef):
            continue
        spec = property_contracts.get(str(predicate))
        if not spec:
            continue
        ranges = [
            str(value)
            for value in spec.get("range_iris") or []
            if str(value)
        ]
        if ranges and not any(node_matches_class(obj, expected) for expected in ranges):
            actual_types = sorted(
                str(value)
                for value in g.objects(obj, RDF.type)
                if isinstance(value, URIRef)
            )
            messages.append(
                "Object-property range mismatch for "
                f"{predicate}: object {obj} has types {actual_types}, "
                f"expected a type compatible with {ranges}"
            )

    for required in ontology_contract.get("required_links") or []:
        subject_class = str(required.get("subject_class_iri") or "")
        predicate_iri = str(required.get("predicate_iri") or "")
        target_class = str(required.get("target_class_iri") or "")
        min_count = int(required.get("min_count") or 0)
        if not subject_class or not predicate_iri or min_count <= 0:
            continue
        for subject in {
            node
            for node in g.subjects(RDF.type, None)
            if isinstance(node, URIRef) and node_matches_class(node, subject_class)
        }:
            if (
                subject != entity_ref
                and str(subject) in (known_top_entity_uris or set())
            ):
                # Other pipeline roots are validated in their own published
                # fragments; this fragment may contain only a typed reference.
                continue
            targets = [
                target
                for target in g.objects(subject, URIRef(predicate_iri))
                if isinstance(target, URIRef)
                and (
                    not target_class
                    or node_matches_class(target, target_class)
                )
            ]
            if len(targets) < min_count:
                messages.append(
                    f"Missing ontology-required link {predicate_iri} on {subject}: "
                    f"expected >= {min_count}, found {len(targets)}"
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
        g = _parse_turtle_path(ttl_path)
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
    ontology_contract: Optional[dict] = None,
) -> tuple[bool, list[str]]:
    """
    Post-publish repair step: merge top shell into the published entity TTL and
    conservatively attach machine-required links to existing typed targets.
    """
    publish_policy = (main_entity_policy or {}).get("publish", {}) or {}
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
        g = _parse_turtle_path(top_ttl)
        g += _parse_turtle_path(ttl_path)
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

    top_class_iri = _ontology_contract_top_class_iri(ontology_contract)
    top_entity = URIRef(str(entity_uri).strip()) if str(entity_uri).strip() else None
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

    for spec in (ontology_contract or {}).get("required_links", []) or []:
        subject_class_iri = str(
            (spec or {}).get("subject_class_iri") or ""
        ).strip()
        pred_iri = str((spec or {}).get("predicate_iri") or "").strip()
        target_class_iri = str((spec or {}).get("target_class_iri") or "").strip()
        min_count = int((spec or {}).get("min_count") or 0)
        if not (top_entity and subject_class_iri and pred_iri and target_class_iri):
            continue
        subject_class = URIRef(subject_class_iri)
        if (top_entity, RDF.type, subject_class) not in g:
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
        chosen_target = _choose_repair_target_for_top_entity(
            g,
            top_entity=top_entity,
            target_cls=target_cls,
        )

        if chosen_target is not None:
            g.add((top_entity, pred, chosen_target))
            messages.append(
                f"Attached machine-required link {pred_iri} to {chosen_target}"
            )

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
def write_global_state(
    doi: str,
    top_level_entity_name: str,
    top_level_entity_iri: str | None = None,
    *,
    data_dir: str | None = None,
):
    """Write the legacy compatibility state inside the active pipeline data root."""
    state_dir = os.path.abspath(
        data_dir or os.environ.get("TWA_AGENTIC_DATA_DIR") or "data"
    )
    state_json = os.path.join(state_dir, "global_state.json")
    state_lock = os.path.join(state_dir, "global_state.lock")
    os.makedirs(state_dir, exist_ok=True)
    lock = FileLock(state_lock)
    lock.acquire(timeout=30.0)
    try:
        state = {"doi": doi, "top_level_entity_name": top_level_entity_name}
        if top_level_entity_iri:
            state["top_level_entity_iri"] = top_level_entity_iri
        fd, tmp = tempfile.mkstemp(dir=state_dir, suffix=".json.tmp")
        os.close(fd)
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            replace_with_retry(tmp, state_json)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
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


def _entity_checkpoint_path(doi_folder: str, entity_scope: str) -> str:
    return os.path.join(doi_folder, "memory", f"{entity_scope}.checkpoint.json")


def _entity_iteration_checkpoint_paths(
    doi_folder: str,
    entity_scope: str,
    iteration: int,
) -> tuple[str, str]:
    directory = os.path.join(
        doi_folder,
        "memory",
        "checkpoints",
        entity_scope,
    )
    return (
        os.path.join(directory, f"iteration_{int(iteration)}.ttl"),
        os.path.join(directory, f"iteration_{int(iteration)}.refs.json"),
    )


def _write_bytes_atomic(path: str, payload: bytes) -> None:
    os.makedirs(_filesystem_path(os.path.dirname(path)), exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=_filesystem_path(os.path.dirname(path)),
        suffix=".checkpoint.tmp",
    )
    os.close(fd)
    try:
        with open(temporary, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, _filesystem_path(path))
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _write_entity_checkpoint(
    *,
    doi_folder: str,
    doi_hash: str,
    entity_scope: str,
    entity_label: str,
    entity_uri: str,
    entity_types: list[str],
    canonical_ttl: str,
    iteration: int,
) -> None:
    """Advance one entity checkpoint only after canonical TTL validation."""
    with open(_filesystem_path(canonical_ttl), "rb") as handle:
        canonical_bytes = handle.read()
    canonical_graph = Graph()
    canonical_graph.parse(
        data=canonical_bytes.decode("utf-8"),
        format="turtle",
    )
    checkpoint_ttl, checkpoint_refs = _entity_iteration_checkpoint_paths(
        doi_folder,
        entity_scope,
        iteration,
    )
    _write_bytes_atomic(checkpoint_ttl, canonical_bytes)
    refs_path = _entity_ref_registry_path(doi_folder, entity_scope)
    if os.path.isfile(refs_path):
        refs_bytes = Path(refs_path).read_bytes()
        _write_bytes_atomic(checkpoint_refs, refs_bytes)
        refs_sha256: str | None = hashlib.sha256(refs_bytes).hexdigest()
    else:
        Path(checkpoint_refs).unlink(missing_ok=True)
        refs_sha256 = None
    payload = {
        "schema_version": "pipeline-entity-checkpoint.v2",
        "doi": doi_hash,
        "entity_scope": entity_scope,
        "entity_label": entity_label,
        "entity_uri": entity_uri,
        "entity_types": sorted({str(value) for value in entity_types if str(value)}),
        "canonical_ttl": os.path.abspath(checkpoint_ttl),
        "ref_registry_snapshot": (
            os.path.abspath(checkpoint_refs) if refs_sha256 else None
        ),
        "ref_registry_sha256": refs_sha256,
        "iteration": int(iteration),
        "ttl_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
        "triple_count": len(canonical_graph),
    }
    _write_json_atomic(_entity_checkpoint_path(doi_folder, entity_scope), payload)


def _restore_entity_iteration_checkpoint(
    *,
    doi_folder: str,
    entity_scope: str,
    iteration: int,
) -> str:
    """Restore one exact validated entity checkpoint, including ref bindings."""
    checkpoint_ttl, checkpoint_refs = _entity_iteration_checkpoint_paths(
        doi_folder,
        entity_scope,
        iteration,
    )
    if not os.path.isfile(checkpoint_ttl):
        raise RuntimeError(
            f"Entity checkpoint TTL is missing for iteration {iteration}: "
            f"{checkpoint_ttl}"
        )
    canonical_ttl = os.path.join(
        doi_folder,
        "memory",
        f"{entity_scope}.ttl",
    )
    _write_bytes_atomic(canonical_ttl, Path(checkpoint_ttl).read_bytes())
    refs_path = _entity_ref_registry_path(doi_folder, entity_scope)
    if os.path.isfile(checkpoint_refs):
        _write_bytes_atomic(refs_path, Path(checkpoint_refs).read_bytes())
    else:
        Path(refs_path).unlink(missing_ok=True)
    return checkpoint_ttl


def _seed_entity_canonical_memory(
    *,
    doi_folder: str,
    entity_scope: str,
    entity_uri: str,
    entity_label: str,
    entity_types: list[str],
    top_class_iri: str,
) -> str:
    """Create an exact-identity scoped seed from Iteration 1, fail closed.

    Writes the locked top-entity identity plus its explicit Iteration 1 outgoing
    facts and referenced-node descriptors into ``memory/{scope}.ttl``.
    """
    source = os.path.join(doi_folder, "iteration_1.ttl")
    if not os.path.isfile(source):
        raise RuntimeError(f"Iteration 1 top shell is missing: {source}")
    source_graph = Graph()
    source_graph.parse(source, format="turtle")
    entity = URIRef(str(entity_uri).strip())
    expected_types = {
        str(value).strip() for value in entity_types if str(value).strip()
    }
    if top_class_iri:
        expected_types.add(top_class_iri)
    asserted_types = {
        str(value)
        for value in source_graph.objects(entity, RDF.type)
        if isinstance(value, URIRef)
    }
    if not any(source_graph.triples((entity, None, None))):
        raise RuntimeError(f"Iteration 1 top shell lacks exact entity URI: {entity_uri}")
    if expected_types and not (expected_types & asserted_types):
        raise RuntimeError(
            f"Iteration 1 entity type mismatch for {entity_uri}: "
            f"asserted={sorted(asserted_types)}, expected={sorted(expected_types)}"
        )

    seed = Graph()
    for triple in source_graph.triples((entity, None, None)):
        seed.add(triple)
        obj = triple[2]
        if isinstance(obj, URIRef):
            for descriptor_predicate in (RDF.type, RDFS.label):
                for descriptor in source_graph.triples(
                    (obj, descriptor_predicate, None)
                ):
                    seed.add(descriptor)
        elif isinstance(obj, BNode):
            pending = [obj]
            visited: set[BNode] = set()
            while pending:
                node = pending.pop()
                if node in visited:
                    continue
                visited.add(node)
                for descriptor in source_graph.triples((node, None, None)):
                    seed.add(descriptor)
                    if isinstance(descriptor[2], BNode):
                        pending.append(descriptor[2])
    for type_iri in sorted(expected_types | asserted_types):
        seed.add((entity, RDF.type, URIRef(type_iri)))
    labels = {str(value) for value in source_graph.objects(entity, RDFS.label)}
    for label in sorted(labels):
        seed.add((entity, RDFS.label, Literal(label)))
    if entity_label and entity_label not in labels:
        seed.add((entity, RDFS.label, Literal(entity_label)))

    target = os.path.join(doi_folder, "memory", f"{entity_scope}.ttl")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(target), suffix=".ttl.tmp")
    os.close(fd)
    try:
        seed.serialize(destination=temporary, format="turtle")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return target


def _validate_canonical_entity_identity(
    *,
    ttl_path: str,
    entity_uri: str,
    entity_types: list[str],
    top_class_iri: str,
) -> tuple[bool, list[str]]:
    """Validate the pipeline-owned identity boundary without iteration semantics."""
    try:
        graph = _parse_turtle_path(ttl_path)
    except Exception as exc:
        return False, [f"Canonical memory is not valid Turtle: {exc}"]
    entity = URIRef(str(entity_uri or "").strip())
    messages: list[str] = []
    if not entity_uri or not any(graph.triples((entity, None, None))):
        messages.append(f"Canonical memory lacks exact entity URI: {entity_uri}")
        return False, messages
    expected_types = {
        str(value).strip() for value in entity_types if str(value).strip()
    }
    if top_class_iri:
        expected_types.add(top_class_iri)
    asserted_types = {
        str(value)
        for value in graph.objects(entity, RDF.type)
        if isinstance(value, URIRef)
    }
    if expected_types and not (asserted_types & expected_types):
        messages.append(
            "Canonical entity type mismatch: "
            f"asserted={sorted(asserted_types)}, expected={sorted(expected_types)}"
        )
    return not messages, messages


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
    lower_name = name.lower()
    sidecar_suffix = next(
        (
            suffix
            for suffix in (".refs.json", ".checkpoint.json")
            if lower_name.endswith(suffix)
        ),
        "",
    )
    if lower_name.endswith(".ttl"):
        stem = os.path.splitext(name)[0]
    elif sidecar_suffix:
        stem = name[: -len(sidecar_suffix)]
    else:
        return False
    if lower_name == "top.ttl":
        return False

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
    order_keys = (contract or {}).get("order_keys") or []
    if not isinstance(order_keys, list) or not order_keys:
        return []
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
        raw_type = raw_type_values[0]
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
        g = _parse_turtle_path(ttl_path)
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


def _decode_structured_hint_prefix(hints_text: str) -> dict:
    """Decode the leading JSON object even when enrichment patches follow it."""
    text = str(hints_text or "").strip()
    text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text, count=1)
    if not text.startswith("{"):
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(text)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _materialize_om2_quantities_from_hints(
    *,
    ttl_path: str,
    raw_hints: list[str],
    ontology_contract: Optional[dict] = None,
) -> tuple[bool, list[str]]:
    """Materialize explicit OM-2-valued ordered-member fields deterministically.

    Generated MCP creators intentionally accept only datatype fields. Object-valued
    quantities therefore require a quantity individual plus a relationship call,
    which an agent can omit even when the canonical hint preserves the value. This
    publish gate reconciles those explicit hints through the active T-Box contract.
    """
    contract = ontology_contract or {}
    class_iris_by_local = {
        _local_name(str(item.get("class_iri") or "")): str(
            item.get("class_iri") or ""
        ).strip()
        for item in contract.get("classes", []) or []
        if str(item.get("class_iri") or "").strip()
    }
    om2_properties: dict[str, tuple[str, str]] = {}
    for item in contract.get("object_properties", []) or []:
        property_iri = str(item.get("property_iri") or "").strip()
        range_iris = [
            str(value).strip()
            for value in item.get("range_iris", []) or []
            if "ontology-of-units-of-measure.org/resource/om-2/" in str(value)
        ]
        if property_iri and len(range_iris) == 1:
            om2_properties[_local_name(property_iri)] = (
                property_iri,
                range_iris[0],
            )
    order_property_iri = next(
        (
            str(item.get("property_iri") or "").strip()
            for item in contract.get("datatype_properties", []) or []
            if _local_name(str(item.get("property_iri") or "")) == "hasOrder"
        ),
        "",
    )

    expectations: dict[tuple[int, str, str], str] = {}
    conflicts: list[str] = []
    for hints_text in raw_hints or []:
        payload = _decode_structured_hint_prefix(hints_text)
        entities = payload.get("entities")
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            class_local = _local_name(
                str(
                    entity.get("class")
                    or entity.get("rdf:type")
                    or entity.get("type")
                    or ""
                )
            )
            if class_local not in class_iris_by_local:
                continue
            property_blocks = [
                block
                for block in (
                    entity.get("datatype_properties"),
                    entity.get("object_properties"),
                    entity.get("properties"),
                )
                if isinstance(block, dict)
            ]
            flattened: dict[str, Any] = {}
            for block in property_blocks:
                flattened.update(block)
            raw_order = (
                flattened.get("hasOrder")
                if "hasOrder" in flattened
                else entity.get("hasOrder")
            )
            try:
                order = int(raw_order)
            except (TypeError, ValueError):
                continue
            for raw_name, raw_value in flattened.items():
                property_local = _local_name(str(raw_name))
                if property_local not in om2_properties:
                    continue
                if isinstance(raw_value, (dict, list)) or raw_value is None:
                    continue
                value = str(raw_value).strip()
                if not value or value.casefold() in {"n/a", "na", "unknown", "-"}:
                    continue
                key = (order, class_local, property_local)
                previous = expectations.get(key)
                if previous is not None and previous != value:
                    conflicts.append(
                        f"Conflicting hinted quantity for order={order} "
                        f"{class_local}.{property_local}: `{previous}` vs `{value}`"
                    )
                expectations[key] = value

    if conflicts:
        return False, conflicts
    if not expectations:
        return True, []
    if not order_property_iri:
        return False, ["Active ontology contract does not expose hasOrder"]

    try:
        graph = _parse_turtle_path(ttl_path)
    except Exception as exc:
        return False, [f"Failed to parse TTL for OM-2 materialization: {exc}"]

    messages: list[str] = []
    errors: list[str] = []
    for (order, class_local, property_local), label in sorted(expectations.items()):
        class_iri = class_iris_by_local[class_local]
        property_iri, quantity_class_iri = om2_properties[property_local]
        matches = [
            node
            for node in graph.subjects(RDF.type, URIRef(class_iri))
            if isinstance(node, URIRef)
            and _step_has_order(
                graph,
                node,
                order,
                order_predicate_iri=order_property_iri,
            )
        ]
        if len(matches) != 1:
            errors.append(
                f"Cannot bind hinted quantity order={order} "
                f"{class_local}.{property_local}: expected one step, found {len(matches)}"
            )
            continue
        step = matches[0]

        def mint_iri(class_name: str, source_label: str) -> URIRef:
            digest = hashlib.sha256(
                (
                    f"{step}|{property_iri}|{quantity_class_iri}|{source_label}"
                ).encode("utf-8")
            ).hexdigest()
            return URIRef(
                "https://www.theworldavatar.com/kg/instance/generated/om2/"
                f"{class_name}/{digest}"
            )

        try:
            quantity = find_or_create_om2_quantity_from_label(
                graph,
                quantity_class=URIRef(quantity_class_iri),
                label=label,
                mint_iri=mint_iri,
            )
        except ValueError as exc:
            errors.append(
                f"Invalid hinted quantity order={order} "
                f"{class_local}.{property_local} `{label}`: {exc}"
            )
            continue
        predicate = URIRef(property_iri)
        existing = set(graph.objects(step, predicate))
        if existing != {quantity}:
            graph.remove((step, predicate, None))
            graph.add((step, predicate, quantity))
            messages.append(
                f"Materialized hinted OM-2 quantity order={order} "
                f"{class_local}.{property_local}=`{label}`"
            )

    if errors:
        return False, [*messages, *errors]
    try:
        graph.serialize(destination=_filesystem_path(ttl_path), format="turtle")
    except Exception as exc:
        return False, [*messages, f"Failed to write OM-2-reconciled TTL: {exc}"]
    return True, messages


def _merge_hint_payloads(base: dict, update: dict) -> dict:
    merged = dict(base or {})
    for key, value in (update or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_hint_payloads(merged[key], value)
        else:
            merged[key] = value
    return merged


def _step_has_order(
    g: Graph, node: URIRef, order: Any, order_predicate_iri: str = ""
) -> bool:
    if not order_predicate_iri:
        return False
    for obj in g.objects(node, URIRef(order_predicate_iri)):
        try:
            return int(obj) == int(order)
        except Exception:
            return str(obj) == str(order)
    return False


def _prune_unhinted_orphan_required_targets(
    *,
    ttl_path: str,
    raw_hints: list[str],
    main_entity_policy: Optional[dict] = None,
    ontology_contract: Optional[dict] = None,
) -> tuple[bool, list[str]]:
    """Remove unlinked required-target placeholders that are not mentioned in hints."""
    required_links = (ontology_contract or {}).get("required_links", []) or []
    if not required_links:
        return True, []
    try:
        g = _parse_turtle_path(ttl_path)
    except Exception as e:
        return False, [f"Failed to parse published TTL for orphan pruning: {e}"]

    raw_text = "\n".join(str(x or "") for x in raw_hints)
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
    ontology_contract: Optional[dict] = None,
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
        g = _parse_turtle_path(ttl_path)
    except Exception as e:
        return False, [f"Failed to parse published TTL for hint reconciliation: {e}"]

    top_entity = URIRef(str(entity_uri).strip()) if str(entity_uri).strip() else None
    if top_entity is None:
        return False, ["Missing top-level entity IRI for hint reconciliation"]

    messages: list[str] = []
    exclusive_property_groups = _get_hint_exclusive_property_groups(main_entity_policy)
    declared_datatype_properties = {
        str(item.get("property_iri") or "").strip()
        for item in (ontology_contract or {}).get("datatype_properties", []) or []
        if str(item.get("property_iri") or "").strip()
    }
    for spec in _get_hint_reconciliation_specs(main_entity_policy):
        section_name = str(spec.get("section_name") or "").strip()
        pred_iri = str(spec.get("predicate_iri") or "").strip()
        target_class_iri = str(spec.get("target_class_iri") or "").strip()
        configured_scalar_properties = {
            str(value).strip()
            for value in spec.get("allowed_scalar_property_iris", [])
            if str(value).strip()
        }
        allowed_scalar_properties = (
            configured_scalar_properties & declared_datatype_properties
        )
        allowed_scalar_by_local = {
            _local_name(value): URIRef(value) for value in allowed_scalar_properties
        }
        if not (section_name and pred_iri and target_class_iri):
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
                g,
                target_cls=target_cls,
                order_predicate_iri=str(
                    spec.get("order_predicate_iri") or ""
                ).strip(),
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

        if not current_targets:
            messages.append(
                f"Skipped {section_name} reconciliation because no source-created typed target exists"
            )
            continue

        target_node = current_targets[0]
        hinted_property_iris = {
            allowed_scalar_by_local[_local_name(str(prop_name))]
            for prop_name, prop_value in hinted_section.items()
            if not isinstance(prop_value, (dict, list))
            and prop_value is not None
            and not str(prop_name).strip().endswith("_label")
            and _local_name(str(prop_name)) in allowed_scalar_by_local
        }
        if bool(spec.get("prune_unhinted_scalar_properties")):
            removed = 0
            for pred_existing, obj_existing in list(g.predicate_objects(target_node)):
                if pred_existing in {RDF.type, RDFS.label}:
                    continue
                if str(pred_existing) not in allowed_scalar_properties:
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
            prop = allowed_scalar_by_local.get(_local_name(str(prop_name)))
            if prop is None:
                continue
            desired_literal = Literal(str(prop_value))
            for old in list(g.objects(target_node, prop)):
                if old != desired_literal:
                    g.remove((target_node, prop, old))
            if (target_node, prop, desired_literal) not in g:
                g.add((target_node, prop, desired_literal))
                messages.append(
                    f"Materialized hinted property {section_name}.{prop_name} on {target_node}"
                )

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


def _persist_partial_kg_attempt(
    *,
    doi_folder: str,
    entity_safe: str,
    entity_label: str,
    entity_uri: str,
    iter_num: int,
    attempt: int,
    candidate_paths: List[str],
    trace: dict[str, Any],
) -> dict[str, Any]:
    """Quarantine a parseable partial graph and its unresolved obligations."""
    partial_dir = (
        Path(doi_folder)
        / "partial_kg_building"
        / f"iter{int(iter_num)}"
    )
    partial_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{entity_safe}.attempt_{int(attempt)}"
    ttl_path = partial_dir / f"{stem}.ttl"
    manifest_path = partial_dir / f"{stem}.manifest.json"
    graph = Graph()
    parsed_sources: list[str] = []
    parse_errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_path in candidate_paths:
        path = str(raw_path or "")
        if not path or not os.path.isfile(path):
            continue
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        try:
            shard = Graph()
            shard.parse(path, format="turtle")
            graph += shard
            parsed_sources.append(path)
        except Exception as exc:
            parse_errors.append(
                {
                    "source": path,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    if graph:
        graph.serialize(destination=str(ttl_path), format="turtle")
        partial_ttl = str(ttl_path)
    else:
        if ttl_path.exists():
            ttl_path.unlink()
        partial_ttl = ""
    unresolved = list(trace.get("unresolved_obligations") or [])
    manifest = {
        "schema_version": "kg-partial-artifact.v1",
        "status": "partial_recoverable" if partial_ttl else "failed_empty",
        "canonical_publish_allowed": False,
        "doi_folder": str(doi_folder),
        "entity_scope": entity_safe,
        "entity_label": entity_label,
        "entity_uri": entity_uri,
        "iteration": int(iter_num),
        "attempt": int(attempt),
        "partial_ttl": partial_ttl,
        "triple_count": len(graph),
        "parsed_sources": parsed_sources,
        "parse_errors": parse_errors,
        "structured_tool_failure": bool(trace.get("structured_tool_failure")),
        "unresolved_obligations": unresolved,
        "automatic_retry_eligible": bool(unresolved)
        and all(bool(item.get("retryable")) for item in unresolved),
    }
    _write_json_atomic(str(manifest_path), manifest)
    manifest["manifest_path"] = str(manifest_path)
    return manifest


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
    if os.path.exists(_filesystem_path(memory_ttl)):
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
                            _filesystem_path(os.path.join(exports_dir, fn)),
                            encoding="utf-8",
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
                with open(_filesystem_path(path), "r", encoding="utf-8") as fh:
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
                mtime = os.path.getmtime(_filesystem_path(path))
            except Exception:
                mtime = 0.0
            return (in_memory_dir, matches_entity_iri, mtime, normalized_path)

        best_candidate = sorted(unique_candidates, key=_score_candidate, reverse=True)[
            0
        ]
        try:
            shutil.copy2(
                _filesystem_path(best_candidate),
                _filesystem_path(intermediate_ttl),
            )
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
        logger.info("    ✅ Saved intermediate TTL from output.ttl")
        return True

    logger.warning(
        f"    ⚠️  No entity TTL found for {entity_label} (safe={entity_safe})"
    )
    return False


def _select_canonical_resume_artifact(
    *,
    doi_folder: str,
    entity_label: str,
    entity_safe: str,
    entity_uri: str = "",
) -> str:
    """Select the canonical existing per-entity TTL path for resume operations."""
    candidates: list[str] = []

    # Prefer exact memory/<safe>.ttl
    memory_ttl = os.path.join(doi_folder, "memory", f"{entity_safe}.ttl")
    if os.path.exists(memory_ttl):
        candidates.append(memory_ttl)

    # Fallback: scan memory/ for other filename variants that normalize to safe
    mem_dir = os.path.join(doi_folder, "memory")
    try:
        if os.path.isdir(mem_dir):
            for fn in os.listdir(mem_dir):
                if not fn.lower().endswith(".ttl") or fn.lower() == "top.ttl":
                    continue
                stem = fn[:-4]
                if _safe_name(stem) == entity_safe:
                    candidates.append(os.path.join(mem_dir, fn))
    except Exception:
        pass

    # Consider exports/ snapshots, using both safe-name and IRI-content matching
    exports_dir = os.path.join(doi_folder, "exports")
    try:
        if os.path.isdir(exports_dir):
            for fn in os.listdir(exports_dir):
                if not fn.lower().endswith(".ttl") or fn.lower() == "top.ttl":
                    continue
                stem = fn[:-4]
                if _safe_name(_strip_export_timestamp(stem)) == entity_safe:
                    candidates.append(os.path.join(exports_dir, fn))
            if entity_uri:
                iri_token = f"<{entity_uri}>"
                for fn in os.listdir(exports_dir):
                    if not fn.lower().endswith(".ttl") or fn.lower() == "top.ttl":
                        continue
                    p = os.path.join(exports_dir, fn)
                    try:
                        with open(p, "r", encoding="utf-8") as fh:
                            if iri_token in fh.read():
                                candidates.append(p)
                    except Exception:
                        continue
    except Exception:
        pass

    if not candidates:
        return ""

    unique = list(dict.fromkeys(candidates))

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

    return sorted(unique, key=_score_candidate, reverse=True)[0]


def _select_richest_entity_checkpoint(
    *,
    doi_folder: str,
    entity_uri: str,
    ontology_output_dir: str,
    intermediate_ttl_dir: str,
) -> str:
    """Select the most complete persisted graph containing the exact entity IRI."""
    candidates: list[str] = []
    for directory in (
        os.path.join(doi_folder, "memory"),
        os.path.join(doi_folder, "exports"),
        ontology_output_dir,
        intermediate_ttl_dir,
    ):
        if not directory or not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if name.lower().endswith(".ttl") and name.lower() != "top.ttl":
                candidates.append(os.path.join(directory, name))

    scored: list[tuple[int, float, str]] = []
    entity = URIRef(entity_uri)
    for path in list(dict.fromkeys(candidates)):
        try:
            graph = _parse_turtle_path(path)
        except Exception:
            continue
        if entity_uri and not any(graph.triples((entity, None, None))):
            continue
        try:
            mtime = os.path.getmtime(_filesystem_path(path))
        except OSError:
            mtime = 0.0
        scored.append((len(graph), mtime, path))
    if not scored:
        return ""
    return max(scored, key=lambda item: (item[0], item[1], item[2]))[2]


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
    ontology_contract: Optional[dict] = None,
    agent_model: str = "gpt-4o",
    entity_scope: str | None = None,
    known_top_entities: Optional[List[dict]] = None,
    identity_dossier: Optional[dict] = None,
    run_label: str = "",
    compiled_iteration_spec: Optional[dict] = None,
    recursion_limit: int | None = None,
    generic_onepass_experiment: bool = False,
    mcp_instruction_in_user: bool = False,
    mcp_runtime_only_experiment: bool = False,
    react_history_projection: bool = False,
    react_argument_firewall: bool = False,
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
    safe = str(entity_scope or _safe_name(entity_label))
    doi_folder = os.path.join(data_dir, doi_hash)

    bound_prompt, binding_warnings = bind_kg_runtime_context(
        kg_prompt,
        doi_hash=doi_hash,
        entity_label=entity_label,
        entity_uri=entity_uri,
        hints_content=hints_content,
        iter_num=iter_num,
        identity_dossier=identity_dossier,
    )
    for warning in binding_warnings:
        logger.warning("    ⚠️  %s", warning)

    def attach_runtime_layers(base: str) -> str:
        layered = inject_global_context_brief(
            base,
            load_global_context_brief(
                Path(doi_folder) / "global_procedure_context.json"
            ),
        )
        if not generic_onepass_experiment and not mcp_runtime_only_experiment:
            layered = _augment_kg_prompt_with_runtime_rules(
                kg_prompt=layered,
                entity_label=entity_label,
                entity_uri=entity_uri,
                doi_hash=doi_hash,
                main_entity_policy=main_entity_policy or {},
                hints_content=hints_content,
                ontology_contract=ontology_contract,
                compiled_iteration_spec=compiled_iteration_spec,
            )
        canonical_top_entities = [
            {
                "label": str(item.get("label") or ""),
                "uri": str(item.get("uri") or ""),
                "types": list(item.get("types") or []),
            }
            for item in (known_top_entities or [])
            if str(item.get("uri") or "").strip()
        ]
        if canonical_top_entities:
            layered += (
                "\n\nGlobal top-entity identity manifest (authoritative across entity fragments):\n"
                + json.dumps(canonical_top_entities, ensure_ascii=False, indent=2)
                + "\n- Reuse these exact URIs for every relationship to another top entity, "
                "including procedure inheritance.\n"
                "- Never create a top-entity instance during main KG iterations. If a referenced "
                "top label is absent from this manifest, report an upstream identity blocker.\n"
            )

        prior_ref_registry = _load_entity_ref_registry(doi_folder, safe)
        existing_artifacts = _entity_persistence_artifacts(
            doi_folder=doi_folder,
            entity_safe=safe,
            entity_label=entity_label,
        )
        if existing_artifacts:
            persisted_inventory = _persisted_abox_entity_inventory(
                existing_artifacts,
                ref_registry=prior_ref_registry,
            )
            if persisted_inventory:
                layered += (
                    "\n\nPersisted A-Box entity inventory (authoritative existing identities):\n"
                    + json.dumps(persisted_inventory, ensure_ascii=False, indent=2)
                    + "\n"
                    "- Resolve any prior hint ref from its exact `refs` entry before considering "
                    "type/label matching. Reuse these IRIs whenever the source hint refers to that "
                    "exact ref. Do not create a generic, placeholder, or renamed duplicate merely "
                    "because the current iteration omits the prior entity record.\n"
                )
        if not generic_onepass_experiment:
            layered += (
                "\n\nGraph lifecycle instructions:\n"
                "- Lifecycle: open_or_resume. Call `init_memory` for this DOI/entity scope before "
                "mutation. It is idempotent and internally resumes canonical persisted state.\n"
                "- No reset/replace/clear or arbitrary-path loader is part of the public lifecycle.\n"
            )
        if not generic_onepass_experiment and not mcp_runtime_only_experiment:
            layered += (
                "\n\n"
                "Before exporting the final TTL/memory, call the tool `check_orphan_entities` to detect any orphan entities. "
                "If any are found, attempt to connect them appropriately to the scoped top entity, ordered members, linked child entities, or parameters. "
                "If you cannot connect some, list their details in your response and proceed with export."
            )
        return layered

    if mcp_instruction_in_user:
        case_bindings = bound_prompt
        case_continuation = attach_runtime_layers("")
        prompt = case_bindings + case_continuation
    else:
        case_bindings = None
        case_continuation = ""
        prompt = attach_runtime_layers(bound_prompt)

    # Save full prompt
    run_suffix = (
        "_" + re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_label).strip())
        if str(run_label).strip()
        else ""
    )
    kg_prompts_dir = os.path.join(
        doi_folder, "prompts", f"iter{iter_num}_kg_building{run_suffix}"
    )
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
            if mcp_instruction_in_user:
                f.write("## Case bindings\n\n")
                f.write(case_bindings or "")
                f.write(
                    "\n\n## MCP instruction\n\n"
                    "Inserted at runtime by BaseAgent from the MCP `instruction` prompt.\n"
                )
                if case_continuation:
                    f.write("\n## Runtime continuation\n")
                    f.write(case_continuation)
            else:
                f.write(prompt)
    except Exception as e:
        logger.warning(f"Failed to save prompt to {prompt_file}: {e}")

    # Write global state for MCP server
    write_global_state(doi_hash, safe, entity_uri, data_dir=data_dir)

    # One rebuild by default. Presence/audit fail-open on this attempt.
    max_retries = _resolve_kg_attempt_limit(main_entity_policy)
    retry_prompt = prompt
    retry_head = case_bindings or prompt
    retry_tail = case_continuation
    graph_mode = "open_or_resume"
    kg_responses_dir = os.path.join(
        doi_folder, "responses", f"iter{iter_num}_kg_building{run_suffix}"
    )
    os.makedirs(_filesystem_path(kg_responses_dir), exist_ok=True)
    retry_state_snapshot = _snapshot_entity_retry_state(
        doi_folder=doi_folder,
        entity_safe=safe,
        entity_label=entity_label,
        ontology_name=str(
            (ontology_contract or {}).get("ontology_name") or ""
        ).strip(),
    )
    for attempt in range(max_retries):
        trace_file = os.path.join(
            kg_responses_dir, f"{safe}.attempt_{attempt + 1}.trace.json"
        )
        try:
            artifacts_before = _entity_persistence_artifacts(
                doi_folder=doi_folder,
                entity_safe=safe,
                entity_label=entity_label,
            )
            artifact_fingerprints_before = _artifact_fingerprints(artifacts_before)
            logger.info(
                f"    🚀 Running KG building agent for '{entity_label}' (iter {iter_num})"
            )
            logger.info(f"    Agent execution attempt {attempt + 1}/{max_retries}")

            async def _run_main_kg_agent():
                BaseAgent = _get_base_agent()
                agent = BaseAgent(
                    model_name=agent_model,
                    model_config=ModelConfig(temperature=0.0, top_p=0.01),
                    remote_model=True,
                    mcp_tools=mcp_tools,
                    mcp_set_name=mcp_set_name,
                    excluded_tool_names=(
                        [
                            "create_"
                            + _local_name(
                                _ontology_contract_top_class_iri(
                                    ontology_contract or {}
                                )
                            )
                        ]
                        if _ontology_contract_top_class_iri(
                            ontology_contract or {}
                        )
                        else []
                    ),
                )
                return await agent.run(
                    retry_head if mcp_instruction_in_user else retry_prompt,
                    recursion_limit=int(
                        recursion_limit or MAIN_KG_RECURSION_LIMIT
                    ),
                    required_initial_tool="init_memory",
                    required_initial_tool_args={
                        "doi": doi_hash,
                        "top_level_entity_name": safe,
                        "root_iri": entity_uri,
                    },
                    required_final_tool="export_memory",
                    required_final_tool_args={
                        "doi": doi_hash,
                        "top_level_entity_name": safe,
                    },
                    mcp_instruction_in_user=mcp_instruction_in_user,
                    task_continuation=(
                        retry_tail if mcp_instruction_in_user else None
                    ),
                    react_history_projection=react_history_projection,
                    react_argument_firewall=react_argument_firewall,
                )

            def _restore_after_transport() -> None:
                _restore_entity_retry_state(retry_state_snapshot)
                write_global_state(doi_hash, safe, entity_uri, data_dir=data_dir)

            response, metadata = await retry_async_on_transport(
                _run_main_kg_agent,
                restore=_restore_after_transport,
                logger=logger,
                what=f"main KG '{entity_label}' iter {iter_num}",
            )
            logger.info(f"    ✅ Agent execution succeeded on attempt {attempt + 1}")

            # CRITICAL: Wait for MCP server operations to complete before proceeding
            # The MCP server is a separate process that may have delayed I/O operations
            logger.info("    ⏳ Waiting for MCP server operations to complete...")
            await asyncio.sleep(3)

            logger.info(
                "    ✅ MCP server operations completed; export_memory was executed "
                "by the agent or the same-session pipeline fallback"
            )

            # Save every attempt independently so retries never erase evidence.
            response_file = os.path.join(
                kg_responses_dir, f"{safe}.attempt_{attempt + 1}.md"
            )
            try:
                with open(_filesystem_path(response_file), "w", encoding="utf-8") as f:
                    f.write(f"# Iteration {iter_num} KG Building Response\n\n")
                    f.write(f"**Entity**: {entity_label}\n\n")
                    f.write(f"**Attempt**: {attempt + 1}/{max_retries}\n\n")
                    f.write("---\n\n")
                    f.write(str(response))
            except Exception as e:
                logger.warning(f"Failed to save response to {response_file}: {e}")

            response_text = str(response)
            artifacts = _entity_persistence_artifacts(
                doi_folder=doi_folder,
                entity_safe=safe,
                entity_label=entity_label,
            )
            structured_turtle = {
                "attempted": False,
                "persisted": False,
                "target": "",
                "message": "",
            }
            if not _tool_trace_has_structured_failure(metadata):
                structured_turtle["attempted"] = True
                structured_turtle["target"] = os.path.join(
                    doi_folder, "memory", f"{safe}.ttl"
                )
                persisted, message = _persist_structured_turtle_result(
                    metadata,
                    target_path=structured_turtle["target"],
                )
                structured_turtle["persisted"] = persisted
                structured_turtle["message"] = message
                if persisted:
                    artifacts = _entity_persistence_artifacts(
                        doi_folder=doi_folder,
                        entity_safe=safe,
                        entity_label=entity_label,
                    )
            artifact_fingerprints_after = _artifact_fingerprints(artifacts)
            fresh_artifacts = [
                path
                for path, fingerprint in artifact_fingerprints_after.items()
                if artifact_fingerprints_before.get(path) != fingerprint
            ]
            current_attempt_artifacts = (
                artifacts if structured_turtle["persisted"] else fresh_artifacts
            )
            successful_mutations = _successful_mutation_tools(metadata)
            blocker_declared = bool(
                re.search(
                    r"(?i)\b(?:upstream[- ](?:identity|materialization)[- ]blocker|"
                    r"semantic[- ]blocker|blocked by upstream)\b",
                    response_text,
                )
            )
            if compiled_iteration_spec is not None:
                (
                    owned_classes,
                    owned_object_properties,
                ) = _compiled_iteration_owned_surface(compiled_iteration_spec)
            else:
                owned_classes, owned_object_properties = (
                    _parse_iteration_owned_surface(kg_prompt)
                )
            fidelity_ok = False
            fidelity_messages: list[str] = []
            fidelity_expectations = 1
            resolved_hint_refs: dict[str, dict[str, Any]] = {}
            framework_integrity_report: dict[str, Any] | None = None
            semantic_graph_report: dict[str, Any] | None = None
            framework_ok = False
            semantic_hint_mode = _is_semantic_hint_content(hints_content)
            fidelity_target = (
                structured_turtle["target"]
                if structured_turtle["persisted"]
                else current_attempt_artifacts[0]
                if current_attempt_artifacts
                else ""
            )
            presence_cfg, use_legacy_fi = _resolve_kg_audit_policy(main_entity_policy)
            presence_report: dict[str, Any] | None = None
            presence_ok = True
            presence_feedback = ""
            if fidelity_target and presence_cfg.get("enabled"):
                presence_report = await asyncio.to_thread(
                    judge_presence_coverage,
                    hints_text=hints_content,
                    abox_path=Path(fidelity_target),
                    tool_activity=(metadata or {}).get("tool_activity") or {},
                    mcp_catalog=catalog_for_groups(
                        presence_cfg.get("mcp_groups")
                        or []
                    ),
                    ontology_contract=ontology_contract,
                    model=str(presence_cfg.get("model") or "gpt-4o"),
                )
                presence_ok = bool(presence_report.get("accepted"))
                presence_feedback = format_presence_coverage_feedback(presence_report)
                if not presence_ok:
                    try:
                        recipe_report = await asyncio.to_thread(
                            propose_tool_recipes,
                            missing=list(presence_report.get("missing") or []),
                            inventory=extract_tool_inventory(
                                kg_prompt,
                                (metadata or {}).get("tool_activity") or {},
                            ),
                            hints_text=hints_content,
                            model=str(presence_cfg.get("model") or "gpt-4o"),
                        )
                    except Exception as exc:
                        logger.warning(
                            "    Presence tool-recipe translator failed: %s",
                            exc,
                        )
                        recipe_report = {"recipes": [], "error": str(exc)}
                    presence_report["tool_recipes"] = recipe_report
                    recipe_text = format_tool_recipe_feedback(
                        list((recipe_report or {}).get("recipes") or [])
                    )
                    if recipe_text:
                        presence_feedback = (
                            presence_feedback.rstrip() + "\n\n" + recipe_text
                        )
                _write_json_atomic(
                    os.path.join(
                        kg_responses_dir,
                        f"{safe}.attempt_{attempt + 1}.presence_coverage_audit.json",
                    ),
                    presence_report,
                )
            replace_llm_audits = bool(
                presence_cfg.get("enabled")
                and presence_cfg.get("replace_llm_audits")
                and not use_legacy_fi
            )
            if fidelity_target and replace_llm_audits:
                framework_integrity_report = presence_report
                framework_ok = presence_ok
                fidelity_ok = presence_ok
                # Agent-facing text must stay rebuild-oriented. The audit JSON
                # keeps matched IRIs for debugging; do not put them in retry messages.
                fidelity_messages = [presence_feedback] if presence_feedback else []
            elif fidelity_target and use_legacy_fi:
                semantic_graph_contract = dict(ontology_contract or {})
                audit_scope_policy = (
                    "Evaluate the current hints against the cumulative graph, limited to "
                    "the iteration-owned classes and object properties. Prior-iteration "
                    "assertions are trusted context. Explicit negative evidence means "
                    "correct omission, while assertions that contradict it are defects. A "
                    "source mention explicitly excluded from the current ownership layer by "
                    "the active T-Box or iteration contract is out of scope: do not enumerate "
                    "it as a required source item or report its omission. If that mention is "
                    "owned by another iteration, leave its materialization to that layer."
                )
                semantic_graph_contract["iteration_audit_scope"] = {
                    "policy": audit_scope_policy,
                    "current_iteration": iter_num,
                    "current_entity": entity_uri,
                    "owned_classes": sorted(owned_classes),
                    "owned_object_properties": sorted(owned_object_properties),
                    "source": (
                        "compiled_iteration_spec"
                        if compiled_iteration_spec is not None
                        else "legacy_prompt_prose"
                    ),
                }
                semantic_graph_contract["framework_integrity_audit"] = {
                    "policy": (
                        "Treat structural integration as a blocking, highest-priority "
                        "obligation. Every source-supported entity materialized for the current "
                        "scope must participate in the source- and T-Box-supported object-property "
                        "structure of that scope. A typed node or literal shell that is detached "
                        "from its intended parent, collection, owner, or related entity is "
                        "incomplete even when the node itself exists. Evaluate this semantically "
                        "from the hints, ontology contract, and candidate graph; do not apply "
                        "domain-specific names or hard-coded count formulas."
                    ),
                    "blocking": True,
                    "priority": "highest",
                }
                framework_integrity_report = await asyncio.to_thread(
                    judge_framework_integrity,
                    document_text=hints_content,
                    ontology_contract=semantic_graph_contract,
                    abox_path=Path(fidelity_target),
                    model=agent_model,
                    reviewer_model=agent_model,
                    verifier_model=agent_model,
                )
                framework_ok = bool(framework_integrity_report.get("accepted"))
                fidelity_messages = [
                    json.dumps(item, ensure_ascii=False)
                    for item in framework_integrity_report.get("observations") or []
                ]
                if not framework_ok and not fidelity_messages:
                    fidelity_messages = [
                        "LLM framework-integrity audit rejected the A-Box: "
                        + json.dumps(
                            framework_integrity_report.get("final") or {},
                            ensure_ascii=False,
                        )
                    ]
                _write_json_atomic(
                    os.path.join(
                        kg_responses_dir,
                        f"{safe}.attempt_{attempt + 1}.framework_integrity_audit.json",
                    ),
                    framework_integrity_report,
                )
                if framework_ok:
                    semantic_contract = dict(semantic_graph_contract)
                    semantic_contract.pop("framework_integrity_audit", None)
                    semantic_graph_report = await asyncio.to_thread(
                        judge_semantic_abox,
                        document_text=hints_content,
                        ontology_contract=semantic_contract,
                        abox_path=Path(fidelity_target),
                        models=[agent_model],
                    )
                    acceptance = semantic_graph_report.get("acceptance") or {}
                    fidelity_ok = bool(acceptance.get("accepted"))
                    if not fidelity_ok:
                        fidelity_messages = [
                            json.dumps(item, ensure_ascii=False)
                            for item in semantic_graph_report.get("observations") or []
                        ] or [
                            "LLM semantic audit rejected the A-Box: "
                            + json.dumps(acceptance, ensure_ascii=False)
                        ]
                    _write_json_atomic(
                        os.path.join(
                            kg_responses_dir,
                            f"{safe}.attempt_{attempt + 1}.semantic_graph_audit.json",
                        ),
                        semantic_graph_report,
                    )
                else:
                    fidelity_ok = False
                if presence_cfg.get("enabled") and not presence_ok:
                    fidelity_ok = False
                    if presence_feedback and presence_feedback not in fidelity_messages:
                        fidelity_messages.append(presence_feedback)

                # Ref indexing is non-blocking bookkeeping for later iterations. The
                # completeness decision above is exclusively LLM-based.
                if not semantic_hint_mode and _hint_fidelity_audit_enabled(
                    main_entity_policy
                ):
                    _validate_hint_fidelity(
                        ttl_path=fidelity_target,
                        hints_content=hints_content,
                        ontology_contract=ontology_contract,
                        owned_classes=owned_classes,
                        owned_object_properties=owned_object_properties,
                        prior_ref_registry=prior_ref_registry,
                        resolved_refs_out=resolved_hint_refs,
                    )
            elif fidelity_target:
                # Presence / fidelity / legacy FI all off: accept the persisted
                # candidate so disabling the judges does not itself trigger rebuilds.
                fidelity_ok = True
                framework_ok = True
                presence_ok = True
                fidelity_messages = []
                logger.info(
                    "    Presence/fidelity audits disabled; accepting persisted candidate"
                )
            nonempty_hints = bool(str(hints_content or "").strip())
            semantic_complete = (
                not blocker_declared
                and (
                    fidelity_ok
                    if fidelity_expectations
                    else bool(successful_mutations)
                    if nonempty_hints
                    else True
                )
            )
            trace = _attempt_trace(
                metadata, artifact_found=bool(current_attempt_artifacts)
            )
            semantic_complete = bool(
                semantic_complete and not trace["structured_tool_failure"]
            )
            audit_exhaustion_accepted = _can_accept_kg_after_audit_exhaustion(
                policy=main_entity_policy,
                final_attempt=attempt == max_retries - 1,
                current_attempt_artifacts=current_attempt_artifacts,
                structured_tool_failure=bool(trace["structured_tool_failure"]),
                blocker_declared=blocker_declared,
                semantic_complete=semantic_complete,
                framework_integrity_report=framework_integrity_report,
                framework_ok=framework_ok,
                semantic_graph_report=semantic_graph_report,
                semantic_graph_ok=fidelity_ok,
            )
            trace.update(
                {
                    "attempt": attempt + 1,
                    "graph_mode": graph_mode,
                    "artifacts": artifacts,
                    "fresh_artifacts": fresh_artifacts,
                    "current_attempt_artifacts": current_attempt_artifacts,
                    "structured_turtle_transport": structured_turtle,
                    "semantic_commit": {
                        "complete": semantic_complete,
                        "blocker_declared": blocker_declared,
                        "unresolved_obligations": list(
                            trace.get("unresolved_obligations") or []
                        ),
                        "argument_firewall_warnings": list(
                            trace.get("argument_firewall_warnings") or []
                        ),
                        "argument_owner_repairs": list(
                            trace.get("argument_owner_repairs") or []
                        ),
                        "successful_mutation_tools": successful_mutations,
                        "hint_fidelity_ok": fidelity_ok,
                        "hint_fidelity_expectations": fidelity_expectations,
                        "hint_fidelity_messages": fidelity_messages,
                        "accepted_after_audit_exhaustion": audit_exhaustion_accepted,
                        "presence_coverage_accepted": presence_ok,
                        "presence_coverage_feedback": presence_feedback,
                        "validation_policy": (
                            "presence_coverage_audit"
                            if replace_llm_audits
                            else (
                                "dedicated_llm_framework_integrity_then_semantic_audit"
                                if use_legacy_fi
                                else "audits_disabled"
                            )
                        ),
                    },
                }
            )
            if audit_exhaustion_accepted:
                warning_payload = {
                    "schema_version": "kg-semantic-audit-warning.v1",
                    "doi": doi_hash,
                    "entity_scope": safe,
                    "entity_label": entity_label,
                    "iteration": iter_num,
                    "attempts_exhausted": max_retries,
                    "candidate_ttl": fidelity_target,
                    "framework_accepted": framework_ok,
                    "semantic_accepted": fidelity_ok,
                    "findings": fidelity_messages,
                    "policy": "nonblocking_after_semantic_exhaustion",
                }
                warning_path = os.path.join(
                    kg_responses_dir, f"{safe}.semantic_audit_warning.json"
                )
                _write_json_atomic(warning_path, warning_payload)
                trace["semantic_commit"]["warning_path"] = warning_path
                logger.warning(
                    "    ⚠️  KG semantic audit budget exhausted for '%s'; "
                    "committing the last persisted, structurally valid candidate",
                    entity_label,
                )
            attempt_failed = (
                not current_attempt_artifacts
                or trace["structured_tool_failure"]
                or (not semantic_complete and not audit_exhaustion_accepted)
            )
            if attempt_failed:
                partial_artifact = _persist_partial_kg_attempt(
                    doi_folder=doi_folder,
                    entity_safe=safe,
                    entity_label=entity_label,
                    entity_uri=entity_uri,
                    iter_num=iter_num,
                    attempt=attempt + 1,
                    candidate_paths=current_attempt_artifacts,
                    trace=trace,
                )
                trace["partial_artifact"] = partial_artifact
                trace["semantic_commit"]["outcome_status"] = partial_artifact[
                    "status"
                ]
                trace["semantic_commit"]["canonical_publish_allowed"] = False
            else:
                trace["semantic_commit"]["outcome_status"] = (
                    "complete_with_warnings"
                    if (
                        audit_exhaustion_accepted
                        or trace.get("argument_firewall_warnings")
                        or trace.get("argument_owner_repairs")
                    )
                    else "complete"
                )
                trace["semantic_commit"]["canonical_publish_allowed"] = True
            _write_json_atomic(trace_file, trace)

            if attempt_failed:
                if attempt < max_retries - 1:
                    logger.warning(
                        "    ♻️  KG attempt for '%s' failed persistence, structured-tool, or semantic-completion validation; retrying",
                        entity_label,
                    )
                    _restore_entity_retry_state(retry_state_snapshot)
                    write_global_state(doi_hash, safe, entity_uri, data_dir=data_dir)
                    graph_mode = "open_or_resume"
                    if mcp_instruction_in_user:
                        retry_head = case_bindings or ""
                        retry_tail = _build_kg_recovery_prompt(
                            base_prompt=case_continuation,
                            entity_label=entity_label,
                            entity_uri=entity_uri,
                            prior_attempt_trace=trace,
                        )
                        retry_prompt = retry_head + retry_tail
                    else:
                        retry_prompt = _build_kg_recovery_prompt(
                            base_prompt=prompt,
                            entity_label=entity_label,
                            entity_uri=entity_uri,
                            prior_attempt_trace=trace,
                        )
                        retry_head = retry_prompt
                        retry_tail = ""
                    await asyncio.sleep(3)
                    continue
                logger.error(
                    "    ❌ KG agent failed structured tool/artifact validation after %d attempts",
                    max_retries,
                )
                raise RuntimeError(
                    "KG agent failed structured tool/artifact validation"
                )

            ref_registry_path = _persist_entity_ref_registry(
                doi_folder=doi_folder,
                entity_scope=safe,
                iteration=iter_num,
                resolved_refs=resolved_hint_refs,
            )
            trace["semantic_commit"]["ref_registry_path"] = ref_registry_path
            trace["semantic_commit"]["resolved_hint_refs"] = resolved_hint_refs
            central_commit = _publish_central_memory_after_semantic_commit(
                ttl_path=fidelity_target,
                ontology_name=str(
                    (ontology_contract or {}).get("ontology_name") or ""
                ).strip(),
                doi_hash=doi_hash,
                entity_scope=safe,
            )
            trace["semantic_commit"]["central_memory"] = central_commit
            _write_json_atomic(trace_file, trace)
            canonical_response_file = os.path.join(kg_responses_dir, f"{safe}.md")
            shutil.copyfile(
                _filesystem_path(response_file),
                _filesystem_path(canonical_response_file),
            )
            return response_text

        except asyncio.CancelledError as e:
            _restore_entity_retry_state(retry_state_snapshot)
            try:
                _merge_attempt_termination_trace(
                    trace_file,
                    base={
                        "attempt": attempt + 1,
                        "graph_mode": graph_mode,
                        "artifacts": _entity_persistence_artifacts(
                            doi_folder=doi_folder,
                            entity_safe=safe,
                            entity_label=entity_label,
                        ),
                    },
                    exc=e,
                )
            except Exception as trace_exc:
                logger.warning(
                    "Failed to persist cancelled KG attempt trace: %s",
                    trace_exc,
                )
            raise
        except Exception as e:
            _restore_entity_retry_state(retry_state_snapshot)
            try:
                from models.BaseAgent import exception_details

                leaf_exceptions = exception_details(e)
            except Exception:
                leaf_exceptions = [
                    {"type": type(e).__name__, "message": str(e)}
                ]
            try:
                _merge_attempt_termination_trace(
                    trace_file,
                    base={
                        "attempt": attempt + 1,
                        "graph_mode": graph_mode,
                        "artifacts": _entity_persistence_artifacts(
                            doi_folder=doi_folder,
                            entity_safe=safe,
                            entity_label=entity_label,
                        ),
                    },
                    exc=e,
                    leaf_exceptions=leaf_exceptions,
                )
            except Exception as trace_exc:
                logger.warning(
                    "Failed to merge terminated KG attempt trace: %s",
                    trace_exc,
                )
            if is_llm_transport_error(e):
                logger.error(
                    "    LLM transport retries exhausted for '%s' iter %s; "
                    "not counting this as a KG attempt",
                    entity_label,
                    iter_num,
                )
                raise
            logger.error(
                "    Agent execution failed on attempt %d/%d: %s; leaf cause(s): %s",
                attempt + 1,
                max_retries,
                e,
                json.dumps(leaf_exceptions, ensure_ascii=False),
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
    """Process all main iterations and publish aggregation entity by entity."""
    all_ok = True
    known_top_entity_uris = {
        str(item.get("uri") or "").strip()
        for item in top_entities
        if str(item.get("uri") or "").strip()
    }
    for idx, entity in enumerate(top_entities):
        entity_ok = await _process_iterations_for_entities(
            doi_hash=doi_hash,
            config=config,
            doi_folder=doi_folder,
            top_entities=[entity],
            iterations=iterations,
            mcp_run_dir=mcp_run_dir,
            data_dir=data_dir,
            project_root=project_root,
            ontology_name=ontology_name,
            iterations_config_path=iterations_config_path,
            known_top_entity_uris=known_top_entity_uris,
        )
        if not entity_ok:
            all_ok = False
        if idx < len(top_entities) - 1:
            logger.info(
                "    🔒 Entity synchronization point (preparing for next entity)..."
            )
            await asyncio.sleep(2)
            logger.info("    ✅ Ready for next entity")
    return all_ok


def _required_link_specs_for_messages(
    messages: list[str],
    ontology_contract: dict,
) -> list[dict[str, Any]]:
    """Return ontology-required links explicitly implicated by validation messages."""
    joined = "\n".join(str(message or "") for message in messages)
    implicated: list[dict[str, Any]] = []
    for raw_spec in ontology_contract.get("required_links") or []:
        if not isinstance(raw_spec, dict):
            continue
        predicate_iri = str(raw_spec.get("predicate_iri") or "").strip()
        if predicate_iri and (
            predicate_iri in joined or _local_name(predicate_iri) in joined
        ):
            implicated.append(dict(raw_spec))
    return implicated


def _select_post_publish_repair_context(
    *,
    messages: list[str],
    ontology_contract: dict,
    contexts: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Route a structural failure to the iteration that owns its predicates."""
    required_links = _required_link_specs_for_messages(messages, ontology_contract)
    failed_properties = {
        _local_name(str(spec.get("predicate_iri") or ""))
        for spec in required_links
        if str(spec.get("predicate_iri") or "").strip()
    }
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for context in contexts:
        owned = {
            _local_name(str(value or ""))
            for value in context.get("owned_properties") or []
            if str(value or "").strip()
        }
        overlap = len(failed_properties & owned)
        try:
            iteration = int(context.get("iteration"))
        except (TypeError, ValueError):
            iteration = 10**6
        ranked.append((overlap, -iteration, context))
    if not ranked:
        return None, required_links
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked[0][2], required_links


def _post_publish_repair_prompt(
    *,
    messages: list[str],
    required_links: list[dict[str, Any]],
) -> str:
    """Build authoritative runtime feedback for a retained-memory repair pass."""
    payload = {
        "failure_kind": "post_publish_structural_validation",
        "priority": "highest",
        "validation_messages": [str(message) for message in messages],
        "implicated_required_links": required_links,
    }
    return (
        "\n\n# PIPELINE POST-PUBLISH STRUCTURAL REPAIR — HIGHEST PRIORITY\n"
        "The graph produced from retained memory failed authoritative structural "
        "validation. Treat this feedback as overriding any earlier prompt instruction "
        "that conflicts with the ontology contract or prevents a required repair.\n"
        "Continue from the existing scoped memory; do not clear it and do not rebuild "
        "unrelated facts. Resolve every listed failure with the available atomic tools. "
        "For a missing required relationship, reuse a compatible target if one exists; "
        "otherwise create an instance of the contract-declared target class and add the "
        "required relationship. Preserve valid existing identities and facts. Check the "
        "repaired graph, then export memory as the final tool call.\n"
        + json.dumps(payload, indent=2, ensure_ascii=False)
    )


def _continuity_repair_prompt(continuity_report: dict[str, Any]) -> str:
    """Build repair feedback from confirmed cross-iteration regressions."""
    regressions = continuity_report.get("confirmed_regressions") or []
    payload = {
        "failure_kind": "cross_iteration_continuity",
        "priority": "highest",
        "confirmed_regressions": regressions,
    }
    return (
        "\n\n# PIPELINE CONTINUITY REPAIR — HIGHEST PRIORITY\n"
        "The final graph lost source-supported facts that existed in an earlier "
        "iteration. Repair every confirmed regression below using the retained "
        "scoped memory and available atomic tools.\n"
        "- Preserve all valid final-graph facts and identities.\n"
        "- Restoring a missing entity as an orphan is not sufficient: connect it "
        "to its intended parent or owner with the T-Box-supported relationship "
        "stated or implied by the iteration hints.\n"
        "- Do not clear memory, create another top entity, or merely describe the repair.\n"
        "- Verify the repaired graph and call export_memory as the final tool call.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n"
    )


def _select_continuity_repair_context(
    *,
    continuity_report: dict[str, Any],
    contexts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Select the iteration context that owns the earliest confirmed regression."""
    iterations: list[int] = []
    for item in continuity_report.get("confirmed_regressions") or []:
        if not isinstance(item, dict):
            continue
        try:
            iterations.append(int(item.get("iteration")))
        except (TypeError, ValueError):
            continue
    for iteration in sorted(set(iterations)):
        for context in contexts:
            try:
                if int(context.get("iteration")) == iteration:
                    return context
            except (TypeError, ValueError):
                continue
    return contexts[-1] if contexts else None


def _write_post_publish_feedback(
    *,
    doi_folder: str,
    entity_scope: str,
    entity_label: str,
    entity_uri: str,
    attempt: int,
    messages: list[str],
    required_links: list[dict[str, Any]],
    repair_context: dict[str, Any] | None,
    retry_status: str,
    post_retry_messages: list[str] | None = None,
    retained_checkpoint_sha256: str = "",
) -> str:
    """Persist a highest-priority KG failure even when the retry resolves it."""
    evidence_id = (
        f"kg_building.post_publish_structural.{entity_scope}.attempt_{attempt}"
    )
    payload = {
        "schema_version": "kg-post-publish-feedback.v1",
        "evidence_id": evidence_id,
        "priority": "highest",
        "priority_rank": 0,
        "stage": "main_kg_building",
        "failure_kind": "post_publish_structural_validation",
        "entity": {
            "scope": entity_scope,
            "label": entity_label,
            "uri": entity_uri,
        },
        "attempt": attempt,
        "validation_messages": [str(message) for message in messages],
        "implicated_required_links": required_links,
        "repair_owner": {
            "iteration": (
                repair_context.get("iteration") if repair_context is not None else None
            ),
            "target_artifact": (
                repair_context.get("prompt_path")
                if repair_context is not None
                else None
            ),
        },
        "retry_status": retry_status,
        "retained_checkpoint_sha256": retained_checkpoint_sha256 or None,
        "post_retry_messages": [
            str(message) for message in (post_retry_messages or [])
        ],
        "prompt_enhancement_directive": (
            "Treat this event as the highest-priority causal evidence. Inspect the "
            "owning KG-building prompt for an absent or contradictory executable "
            "instruction, even when the same-run KG retry resolved the graph."
        ),
    }
    path = os.path.join(
        doi_folder,
        "post_publish_feedback",
        entity_scope,
        f"structural_attempt_{attempt}.json",
    )
    _write_json_atomic(path, payload)
    return path


def _next_post_publish_feedback_attempt(
    *, doi_folder: str, entity_scope: str
) -> int:
    """Return the next append-only structural-feedback sequence number."""
    directory = os.path.join(doi_folder, "post_publish_feedback", entity_scope)
    if not os.path.isdir(directory):
        return 1
    attempts: list[int] = []
    for name in os.listdir(directory):
        match = re.fullmatch(r"structural_attempt_(\d+)\.json", name)
        if match:
            attempts.append(int(match.group(1)))
    return max(attempts, default=0) + 1


async def _revise_hints_after_kg_contract_violation(
    *,
    doi_hash: str,
    config: dict,
    iteration: dict[str, Any],
    entity_label: str,
    entity_safe: str,
    violations: list[dict[str, Any]],
    data_dir: str,
) -> bool:
    """Rerun the enrichment sub-iteration that authored an invalid relation."""
    iter_num = int(iteration.get("iteration_number") or 0)
    relation = violations[0] if violations else {}
    relation_tokens = {
        str(relation.get("property") or ""),
        str(relation.get("subject_ref") or ""),
        str(relation.get("object_ref") or ""),
    }
    sub_iterations = list(iteration.get("sub_iterations") or [])
    selected_sub_iteration: dict[str, Any] | None = None
    selected_index = -1
    for sub_index, sub_iteration in enumerate(sub_iterations):
        sub_num = str(sub_iteration.get("iteration_number") or "")
        response_path = os.path.join(
            data_dir,
            doi_hash,
            "responses",
            f"iter{sub_num}_enrichment",
            f"{entity_safe}.md",
        )
        try:
            response_text = Path(response_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        if all(token and token in response_text for token in relation_tokens):
            selected_sub_iteration = sub_iteration
            selected_index = sub_index
            break
    if selected_sub_iteration is None:
        current_hint_path = os.path.join(
            data_dir,
            doi_hash,
            "mcp_run",
            f"iter{iter_num}_hints_{entity_safe}.txt",
        )
        try:
            current_hint_text = Path(current_hint_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            current_hint_text = ""
        if (
            sub_iterations
            and all(token and token in current_hint_text for token in relation_tokens)
        ):
            selected_sub_iteration = sub_iterations[0]
            selected_index = 0
            logger.warning(
                "    ⚠️  Invalid relation is present in authoritative iteration %s "
                "hints but absent from retained enrichment responses; restarting all "
                "enrichment sub-iterations for %s",
                iter_num,
                entity_label,
            )
        else:
            logger.error(
                "    ❌ Cannot attribute KG hint violation to an enrichment "
                "sub-iteration or authoritative hints for %s iteration %s",
                entity_label,
                iter_num,
            )
            return False

    impacted_sub_iterations = sub_iterations[selected_index:]
    impacted_sub_nums = [
        str(item.get("iteration_number") or "")
        for item in impacted_sub_iterations
        if str(item.get("iteration_number") or "")
    ]
    sub_num = impacted_sub_nums[0]
    base_hint_path = os.path.join(
        data_dir,
        doi_hash,
        "mcp_run",
        f"iter{iter_num}_hints_{entity_safe}.txt",
    )
    snapshot_path = os.path.join(
        data_dir,
        doi_hash,
        "mcp_run",
        f"iter{iter_num}_base_hints_{entity_safe}.txt",
    )
    if not os.path.isfile(snapshot_path):
        logger.error(
            "    ❌ Cannot revise invalid hints without pre-enrichment snapshot: %s",
            snapshot_path,
        )
        return False
    shutil.copy2(_filesystem_path(snapshot_path), _filesystem_path(base_hint_path))

    doi_folder = os.path.join(data_dir, doi_hash)
    checkpoints_root = Path(doi_folder) / "memory" / "checkpoints"
    if checkpoints_root.is_dir():
        for scope_dir in checkpoints_root.glob(f"{entity_safe}*"):
            for checkpoint_file in scope_dir.glob("iteration_*.*"):
                match = re.match(r"iteration_(\d+)\.", checkpoint_file.name)
                if match and int(match.group(1)) >= iter_num:
                    checkpoint_file.unlink(missing_ok=True)
    intermediate_root = Path(doi_folder) / "intermediate_ttl_files"
    if intermediate_root.is_dir():
        for intermediate_file in intermediate_root.glob(
            f"iteration_*_{entity_safe}*.ttl"
        ):
            match = re.match(r"iteration_(\d+)_", intermediate_file.name)
            if match and int(match.group(1)) >= iter_num:
                intermediate_file.unlink(missing_ok=True)
    responses_root = Path(doi_folder) / "responses"
    if responses_root.is_dir():
        for response_dir in responses_root.glob("iter*_kg_building"):
            match = re.match(r"iter(\d+)_kg_building", response_dir.name)
            if not match or int(match.group(1)) < iter_num:
                continue
            for response_file in response_dir.glob(f"{entity_safe}*"):
                response_file.unlink(missing_ok=True)

    for impacted_sub_iteration in impacted_sub_iterations:
        outputs = impacted_sub_iteration.get("outputs") or {}
        for output_key in ("done_marker", "file_path"):
            template = str(outputs.get(output_key) or "")
            if not template:
                continue
            output_path = os.path.join(
                data_dir,
                doi_hash,
                template.replace("{entity_safe}", entity_safe),
            )
            Path(output_path).unlink(missing_ok=True)

    revision_config = dict(config)
    revision_config.update(
        {
            "_entity_first_entity_safe": entity_safe,
            "only_extraction_iterations": [iter_num],
            "only_extraction_sub_iterations": impacted_sub_nums,
            "skip_parent_extraction_when_targeting_sub_iterations": True,
            "force_extraction_sub_iterations": True,
            "_kg_hint_revision_feedback": json.dumps(
                {
                    "schema_version": "kg-hint-contract-revision.v1",
                    "violations": violations,
                },
                indent=2,
                ensure_ascii=False,
            ),
        }
    )
    from src.pipelines.main_ontology_extractions.extract import (
        run_step as run_extraction_step,
    )

    logger.warning(
        "    🔁 Revising extraction hints via sub-iteration %s before restarting "
        "KG iteration %s for %s",
        sub_num,
        iter_num,
        entity_label,
    )
    return bool(
        await asyncio.to_thread(
            run_extraction_step,
            doi_hash,
            revision_config,
        )
    )


async def _process_iterations_for_entities(
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
    known_top_entity_uris: Optional[set[str]] = None,
) -> bool:
    """Process iterations for the supplied entity batch and aggregate its publish."""
    config = apply_disable_kg_revisions(config)
    source_iterations = list(iterations)
    iterations = collapse_kg_iterations_for_full_hints_onepass(
        iterations, enabled=bool(config.get("kg_full_hints_onepass"))
    )
    if config.get("kg_full_hints_onepass"):
        logger.info(
            "  📘 Full-hints one-pass KG building enabled "
            "(union of iter2/3/4 ledgers, no iteration loop)"
        )
    meta_task_config_path = config.get(
        "meta_task_config", "configs/meta_task/meta_task_config.json"
    )
    meta_cfg = load_meta_task_config(meta_task_config_path)
    main_entity_policy = dict(_get_main_entity_kg_policy(meta_cfg))
    if isinstance(config.get("presence_coverage_audit"), dict):
        main_entity_policy["presence_coverage_audit"] = dict(
            config.get("presence_coverage_audit") or {}
        )
    if isinstance(config.get("continuity_audit"), dict):
        main_entity_policy["continuity_audit"] = dict(
            config.get("continuity_audit") or {}
        )
    if isinstance(config.get("hint_fidelity_audit"), dict):
        main_entity_policy["hint_fidelity_audit"] = dict(
            config.get("hint_fidelity_audit") or {}
        )
    if config.get("kg_max_attempts") is not None:
        main_entity_policy["max_attempts"] = _resolve_kg_attempt_limit(
            {"max_attempts": config.get("kg_max_attempts")}
        )
    if config.get("post_publish_structural_retries") is not None:
        try:
            main_entity_policy["post_publish_structural_retries"] = max(
                0, int(config.get("post_publish_structural_retries"))
            )
        except (TypeError, ValueError):
            main_entity_policy["post_publish_structural_retries"] = 0
    if config.get("continuity_audit_retries") is not None:
        try:
            main_entity_policy["continuity_audit_retries"] = max(
                0, int(config.get("continuity_audit_retries"))
            )
        except (TypeError, ValueError):
            main_entity_policy["continuity_audit_retries"] = 0
    try:
        ontology_publish_contract = build_ontology_publish_contract(
            meta_task_config_path=meta_task_config_path,
            ontology_name=ontology_name or None,
        )
    except Exception as exc:
        logger.error("❌ Failed to build ontology publish contract: %s", exc)
        return False
    agent_model = ((meta_cfg or {}).get("ontologies", {}).get("main", {}) or {}).get(
        "agent_model"
    ) or "gpt-4o"
    output_naming = get_output_naming_config(
        meta_cfg=meta_cfg, ontology_name=ontology_name
    )
    ontology_output_dir = os.path.join(doi_folder, output_naming.output_dir)
    intermediate_ttl_dir = os.path.join(doi_folder, "intermediate_ttl_files")
    os.makedirs(intermediate_ttl_dir, exist_ok=True)
    resume_from_published = bool(
        config.get("resume_main_kg_from_published_state")
    )
    publish_failures = 0
    runtime_ordered_member_profile = load_all_runtime_ordered_member_profiles(
        meta_cfg=meta_cfg,
        project_root=project_root,
    )
    entity_publish_inputs: Dict[str, Dict[str, object]] = {}
    entity_runtime_reset_done: set[str] = set()
    failed_entities: set[str] = set()
    restart_iteration = int(config.get("start_main_kg_iteration") or 2)
    for entity in top_entities:
        entity_label = entity.get("label", "")
        entity_uri = entity.get("uri", "")
        safe = entity_scope_name(entity_label, entity_uri)
        entity_publish_inputs[safe] = {
            "entity_label": entity_label,
            "entity_uri": entity_uri,
            "sources": [],
            "hints": {},
            "raw_hints": [],
            "repair_contexts": [],
            "identity_dossier": dict(entity.get("identity_dossier") or {}),
        }

    # Process iterations 2, 3, 4 (skip iteration 1 - handled by top_entity_kg_building)
    for iteration in iterations:
        iter_num = iteration.get("iteration_number")
        if iter_num == 1:
            continue  # Skip iteration 1
        if int(iter_num) < restart_iteration:
            continue
        stop_iteration = config.get("stop_main_kg_iteration")
        if stop_iteration and int(iter_num) > int(stop_iteration):
            continue

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
        onepass = bool(iteration.get("full_hints_onepass"))
        if onepass:
            public_tool_surface = resolve_generated_mcp_tool_surface(
                mcp_set_name=mcp_set_name,
                mcp_tools=mcp_tools,
                project_root=project_root,
            )
            projection_experiment = config.get(
                "kg_mcp_native_onepass_projection_experiment"
            )
            if config.get("kg_semantic_surface_no_contract_experiment"):
                kg_prompt = build_mcp_semantic_surface_task_prompt()
            elif projection_experiment == "user_aligned":
                kg_prompt = build_mcp_native_onepass_user_aligned_task_prompt()
            elif projection_experiment:
                kg_prompt = build_mcp_native_onepass_task_prompt()
            elif config.get("kg_generic_onepass_prompt_experiment"):
                kg_prompt = build_generic_onepass_kg_prompt()
            else:
                runtime_relationship_contract = resolve_generated_mcp_relationship_contract(
                    mcp_set_name=mcp_set_name,
                    mcp_tools=mcp_tools,
                    project_root=project_root,
                )
                kg_prompt = build_onepass_kg_prompt(
                    iterations=source_iterations,
                    project_root=project_root,
                    load_prompt=load_prompt,
                    allowed_tool_names=public_tool_surface,
                    runtime_relationship_contract=runtime_relationship_contract,
                )
        else:
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
            legacy_safe = _safe_name(entity_label)
            hint_safe = entity_artifact_name(entity_label)
            safe = entity_scope_name(entity_label, entity_uri)

            if safe in failed_entities:
                logger.warning(
                    "  ⛔ Skipping iteration %s for %s because an earlier "
                    "iteration failed",
                    iter_num,
                    entity_label,
                )
                continue

            logger.info(f"  📌 Entity {idx + 1}/{len(top_entities)}: {entity_label}")
            _apply_entity_context_runtime_env(
                main_entity_policy=main_entity_policy,
                entity_safe=safe,
                entity_uri=entity_uri,
            )

            if safe not in entity_runtime_reset_done:
                if resume_from_published:
                    retained_checkpoint = _select_richest_entity_checkpoint(
                        doi_folder=doi_folder,
                        entity_uri=entity_uri,
                        ontology_output_dir=ontology_output_dir,
                        intermediate_ttl_dir=intermediate_ttl_dir,
                    )
                    if not retained_checkpoint:
                        logger.error(
                            "    ❌ Resume requested but no retained checkpoint "
                            "contains exact entity URI %s",
                            entity_uri,
                        )
                        return False
                    scoped_memory = os.path.join(
                        doi_folder, "memory", f"{safe}.ttl"
                    )
                    os.makedirs(os.path.dirname(scoped_memory), exist_ok=True)
                    if os.path.normcase(
                        os.path.abspath(retained_checkpoint)
                    ) != os.path.normcase(os.path.abspath(scoped_memory)):
                        shutil.copy2(
                            _filesystem_path(retained_checkpoint),
                            _filesystem_path(scoped_memory),
                        )
                    checkpoint_sha256 = hashlib.sha256(
                        Path(scoped_memory).read_bytes()
                    ).hexdigest()
                    entity_publish_inputs[safe]["resume_checkpoint"] = scoped_memory
                    entity_publish_inputs[safe][
                        "resume_checkpoint_sha256"
                    ] = checkpoint_sha256
                    logger.info(
                        "    ♻️  Resuming exact retained KG checkpoint without "
                        "canonical reset: %s (sha256=%s)",
                        scoped_memory,
                        checkpoint_sha256[:12],
                    )
                elif restart_iteration > 2:
                    prior_iteration = restart_iteration - 1
                    try:
                        retained_checkpoint = _restore_entity_iteration_checkpoint(
                            doi_folder=doi_folder,
                            entity_scope=safe,
                            iteration=prior_iteration,
                        )
                    except RuntimeError as exc:
                        logger.error(
                            "    ❌ Cannot restart KG iteration %s without prior "
                            "iteration checkpoint: %s",
                            restart_iteration,
                            exc,
                        )
                        failed_entities.add(safe)
                        continue
                    sources = entity_publish_inputs[safe].get("sources")
                    if isinstance(sources, list):
                        sources.append(retained_checkpoint)
                    logger.info(
                        "    ♻️  Restarting from clean iteration %s checkpoint: %s",
                        prior_iteration,
                        retained_checkpoint,
                    )
                else:
                    entity_types = [
                        str(value)
                        for value in entity.get("types", [])
                        if str(value)
                    ]
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
                    scoped_memory = _seed_entity_canonical_memory(
                        doi_folder=doi_folder,
                        entity_scope=safe,
                        entity_uri=entity_uri,
                        entity_label=entity_label,
                        entity_types=entity_types,
                        top_class_iri=_ontology_contract_top_class_iri(
                            ontology_publish_contract
                        ),
                    )
                    _write_entity_checkpoint(
                        doi_folder=doi_folder,
                        doi_hash=doi_hash,
                        entity_scope=safe,
                        entity_label=entity_label,
                        entity_uri=entity_uri,
                        entity_types=entity_types,
                        canonical_ttl=scoped_memory,
                        iteration=1,
                    )
                    logger.info(
                        "    ✅ Restored complete scoped baseline from "
                        "Iteration 1: %s",
                        scoped_memory,
                    )
                entity_runtime_reset_done.add(safe)

            response_file = os.path.join(
                doi_folder, "responses", f"iter{iter_num}_kg_building", f"{safe}.md"
            )
            intermediate_ttl = os.path.join(
                intermediate_ttl_dir, f"iteration_{iter_num}_{safe}.ttl"
            )
            hints_file = _find_hints_file(
                mcp_run_dir=mcp_run_dir,
                iter_num=iter_num,
                entity_safe=hint_safe,
            )
            combined_hint_paths: list[str] = []
            if onepass:
                try:
                    bundle = combine_hint_ledgers(mcp_run_dir, hint_safe)
                except FileNotFoundError as exc:
                    logger.warning("    ⚠️  Full-hints combine failed: %s", exc)
                    continue
                combined_path = Path(mcp_run_dir) / f"full_hints_{hint_safe}.txt"
                combined_path.write_text(bundle.text, encoding="utf-8")
                hints_file = str(combined_path)
                combined_hint_paths = [str(path) for path in bundle.paths]
                logger.info(
                    "    📘 Combined full-hints layers=%s for %s",
                    list(bundle.layers),
                    entity_label,
                )
            freshness_deps = [
                iterations_config_path,
                kg_building_prompt_path,
                hints_file,
                __file__,
                publish_ttl.__code__.co_filename,
                *combined_hint_paths,
            ]
            if onepass:
                freshness_deps.extend(
                    str(
                        item.get("kg_building_onepass_prompt")
                        or item.get("kg_building_prompt")
                        or ""
                    )
                    for item in source_iterations
                    if int(item.get("iteration_number") or 0) >= 2
                )
            freshness_deps.extend(
                resolve_generated_file(
                    f"ai_generated_contents/scripts/{ontology_name}/{filename}",
                    project_root=project_root,
                )
                for filename in (
                    "_relationship_contract.json",
                    "_fixed_rdf_runtime.py",
                    "_fixed_om2_runtime.py",
                    f"{ontology_name}_creation_relationships.py",
                    "main.py",
                )
            )
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
            patch_files = (
                []
                if _is_semantic_hint_content(hints_content)
                else _find_enrichment_patch_files(
                    mcp_run_dir=mcp_run_dir,
                    iter_num=iter_num,
                    entity_safe=hint_safe,
                )
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

            prior_ref_registry = _load_entity_ref_registry(doi_folder, safe)
            hint_violations = _validate_hint_relation_contract(
                hints_content=hints_content,
                ontology_contract=ontology_publish_contract,
                prior_ref_registry=prior_ref_registry,
                iteration=int(iter_num),
            )
            revision_attempt = 0
            max_hint_revisions = int(config.get("kg_hint_revision_max_attempts", 2))
            while hint_violations and revision_attempt < max_hint_revisions:
                revision_attempt += 1
                feedback_path = os.path.join(
                    doi_folder,
                    "kg_hint_feedback",
                    safe,
                    f"iteration_{iter_num}_attempt_{revision_attempt}.json",
                )
                _write_json_atomic(
                    feedback_path,
                    {
                        "schema_version": "kg-hint-contract-feedback.v1",
                        "entity_scope": safe,
                        "entity_label": entity_label,
                        "entity_uri": entity_uri,
                        "iteration": int(iter_num),
                        "revision_attempt": revision_attempt,
                        "violations": hint_violations,
                    },
                )
                revised = await _revise_hints_after_kg_contract_violation(
                    doi_hash=doi_hash,
                    config=config,
                    iteration=iteration,
                    entity_label=entity_label,
                    entity_safe=hint_safe,
                    violations=hint_violations,
                    data_dir=data_dir,
                )
                if not revised:
                    break
                with open(hints_file, "r", encoding="utf-8") as handle:
                    hints_content = handle.read()
                hint_violations = _validate_hint_relation_contract(
                    hints_content=hints_content,
                    ontology_contract=ontology_publish_contract,
                    prior_ref_registry=prior_ref_registry,
                    iteration=int(iter_num),
                )
            if hint_violations:
                logger.error(
                    "    ❌ Extraction hints violate immutable KG relation "
                    "contracts after %d revision attempt(s): %s",
                    revision_attempt,
                    json.dumps(hint_violations, ensure_ascii=False),
                )
                failed_entities.add(safe)
                continue

            _apply_entity_context_runtime_env(
                main_entity_policy=main_entity_policy,
                entity_safe=safe,
                entity_uri=entity_uri,
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
            repair_contexts = entity_publish_inputs.get(safe, {}).get(
                "repair_contexts"
            )
            if isinstance(repair_contexts, list):
                repair_contexts.append(
                    {
                        "iteration": int(iter_num),
                        "owned_properties": list(
                            (iteration.get("responsibilities") or {}).get(
                                "object_properties"
                            )
                            or []
                        ),
                        "kg_prompt": kg_prompt,
                        "prompt_path": resolve_generated_file(
                            kg_building_prompt_path, project_root=project_root
                        ),
                        "hints_content": hints_content,
                        "mcp_tools": list(mcp_tools),
                        "mcp_set_name": mcp_set_name,
                        "agent_model": agent_model,
                        "compiled_iteration_spec": dict(iteration),
                    }
                )
            if resume_from_published:
                retained_source = str(
                    entity_publish_inputs[safe].get("resume_checkpoint") or ""
                )
                sources = entity_publish_inputs[safe].get("sources")
                if (
                    retained_source
                    and isinstance(sources, list)
                    and retained_source not in sources
                ):
                    sources.append(retained_source)
                logger.info(
                    "    ⏭️  Preserved retained KG state; skipping normal "
                    "Iteration %s replay",
                    iter_num,
                )
                continue

            # Reuse only if the prior response + intermediate TTL are newer than the
            # current iterations config, KG prompt, and extracted hints.
            if _artifact_is_current(
                response_file, freshness_deps, project_root=project_root
            ) and _artifact_is_current(
                intermediate_ttl, freshness_deps, project_root=project_root
            ):
                logger.info("    ⏭️  KG building already completed")
                sources = entity_publish_inputs.get(safe, {}).get("sources", [])
                if isinstance(sources, list) and intermediate_ttl not in sources:
                    sources.append(intermediate_ttl)
                continue
            if os.path.exists(response_file) or os.path.exists(intermediate_ttl):
                logger.info(
                    f"    🔁 Existing KG building artifacts are stale for '{entity_label}', regenerating"
                )

            # Wrap the whole iteration, including pipeline-owned post-agent gates.
            # The inner agent retry snapshot cannot protect against failures that
            # occur after the agent has returned successfully.
            iteration_state_snapshot = _snapshot_entity_retry_state(
                doi_folder=doi_folder,
                entity_safe=safe,
                entity_label=entity_label,
                ontology_name=ontology_name,
            )

            # Run KG building agent
            try:
                await run_kg_building_agent(
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
                    ontology_contract=ontology_publish_contract,
                    agent_model=agent_model,
                    entity_scope=safe,
                    known_top_entities=top_entities,
                    identity_dossier=dict(entity.get("identity_dossier") or {}),
                    compiled_iteration_spec=dict(iteration),
                    recursion_limit=(
                        MAIN_KG_ONEPASS_RECURSION_LIMIT if onepass else None
                    ),
                    generic_onepass_experiment=bool(
                        config.get("kg_generic_onepass_prompt_experiment")
                    ),
                    mcp_instruction_in_user=(
                        config.get("kg_mcp_native_onepass_projection_experiment")
                        == "user_aligned"
                    ),
                    mcp_runtime_only_experiment=bool(
                        config.get("kg_semantic_surface_no_contract_experiment")
                    ),
                    react_history_projection=bool(
                        config.get("kg_react_history_projection")
                    ),
                    react_argument_firewall=bool(
                        config.get("kg_argument_firewall_experiment")
                    ),
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
                        if os.path.exists(_filesystem_path(mem_path)):
                            shutil.copy2(
                                _filesystem_path(mem_path),
                                _filesystem_path(intermediate_ttl),
                            )
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
                            if os.path.exists(_filesystem_path(candidate)):
                                shutil.copy2(
                                    _filesystem_path(candidate),
                                    _filesystem_path(intermediate_ttl),
                                )
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
                    disable_posthoc_semantic_processing = bool(
                        config.get("disable_kg_posthoc_semantic_processing")
                    )
                    identity_ok, identity_messages = (
                        (True, [])
                        if disable_posthoc_semantic_processing
                        else _validate_canonical_entity_identity(
                            ttl_path=intermediate_ttl,
                            entity_uri=entity_uri,
                            entity_types=[
                                str(value)
                                for value in entity.get("types", [])
                                if str(value)
                            ],
                            top_class_iri=_ontology_contract_top_class_iri(
                                ontology_publish_contract
                            ),
                        )
                    )
                    if not identity_ok:
                        raise RuntimeError(
                            "KG output failed pipeline-owned entity identity validation: "
                            + "; ".join(identity_messages)
                        )
                    step_ok, step_msgs = (
                        (True, [])
                        if disable_posthoc_semantic_processing
                        else _validate_ordered_members_against_hints(
                            ttl_path=intermediate_ttl,
                            hints_content=hints_content,
                            main_entity_policy=main_entity_policy,
                        )
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
                    om2_ok, om2_messages = (
                        (True, [])
                        if disable_posthoc_semantic_processing
                        else _materialize_om2_quantities_from_hints(
                            ttl_path=intermediate_ttl,
                            raw_hints=[hints_content],
                            ontology_contract=ontology_publish_contract,
                        )
                    )
                    for message in om2_messages:
                        logger.info(f"    🌡️ {message}")
                    if not om2_ok:
                        raise RuntimeError(
                            "KG output failed hinted OM-2 quantity materialization: "
                            + "; ".join(om2_messages[:5])
                        )
                    sources = entity_publish_inputs.get(safe, {}).get("sources", [])
                    if isinstance(sources, list) and intermediate_ttl not in sources:
                        sources.append(intermediate_ttl)
                    _write_entity_checkpoint(
                        doi_folder=doi_folder,
                        doi_hash=doi_hash,
                        entity_scope=safe,
                        entity_label=entity_label,
                        entity_uri=entity_uri,
                        entity_types=[
                            str(value)
                            for value in entity.get("types", [])
                            if str(value)
                        ],
                        canonical_ttl=intermediate_ttl,
                        iteration=int(iter_num),
                    )

            except Exception as e:
                _restore_entity_retry_state(iteration_state_snapshot)
                logger.error(f"    ❌ KG building failed for '{entity_label}': {e}")
                failed_entities.add(safe)
                continue

            # CRITICAL: Synchronization point between entities
            # Wait to ensure all MCP server file operations are flushed to disk
            # before moving to next entity (which will overwrite global state)
            if idx < len(top_entities) - 1:  # Not the last entity
                logger.info(
                    "    🔒 Entity synchronization point (preparing for next entity)..."
                )
                await asyncio.sleep(2)
                logger.info("    ✅ Ready for next entity")

    # Publish once per entity from the accumulated iteration shards.
    for safe, info in entity_publish_inputs.items():
        entity_label = str(info.get("entity_label") or safe)
        entity_uri = str(info.get("entity_uri") or "")
        if safe in failed_entities:
            logger.error(
                "    ❌ Refusing to publish incomplete entity after iteration "
                "failure: %s",
                entity_label,
            )
            publish_failures += 1
            continue
        sources = info.get("sources") or []
        if not isinstance(sources, list) or not sources:
            logger.warning(
                f"    ⚠️  No intermediate TTLs accumulated for {entity_label}"
            )
            publish_failures += 1
            continue
        aggregated_hints = info.get("hints") or {}
        raw_hints = info.get("raw_hints") or []
        disable_posthoc_semantic_processing = bool(
            config.get("disable_kg_posthoc_semantic_processing")
        )

        published = publish_ttl(
            doi_hash=doi_hash,
            ontology_name=ontology_name,
            entity_safe=safe,
            entity_uri=entity_uri,
            entity_label=entity_label,
            ontology_contract=ontology_publish_contract,
            data_dir=data_dir,
            meta_cfg=meta_cfg,
            src_candidates=sources,
            apply_semantic_processing=not disable_posthoc_semantic_processing,
        )
        if published:
            repaired, repair_msgs = (
                (True, [])
                if disable_posthoc_semantic_processing
                else _repair_published_entity_ttl(
                    ttl_path=published,
                    doi_folder=doi_folder,
                    ontology_name=ontology_name,
                    entity_uri=entity_uri,
                    entity_label=entity_label,
                    meta_cfg=meta_cfg,
                    main_entity_policy=main_entity_policy,
                    ontology_contract=ontology_publish_contract,
                )
            )
            if repair_msgs:
                for msg in repair_msgs:
                    logger.info(f"    🔧 {msg}")
            if not repaired:
                logger.error("    ❌ Published TTL repair failed:")
                for msg in repair_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1
            hints_repaired, hints_msgs = (
                (True, [])
                if disable_posthoc_semantic_processing
                else _repair_published_entity_ttl_from_hints(
                    ttl_path=published,
                    entity_uri=entity_uri,
                    entity_label=entity_label,
                    aggregated_hints=aggregated_hints
                    if isinstance(aggregated_hints, dict)
                    else {},
                    ontology_name=ontology_name,
                    main_entity_policy=main_entity_policy,
                    ontology_contract=ontology_publish_contract,
                )
            )
            if hints_msgs:
                for msg in hints_msgs:
                    logger.info(f"    🧭 {msg}")
            if not hints_repaired:
                logger.error("    ❌ Published TTL hint reconciliation failed:")
                for msg in hints_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1

            om2_ok, om2_msgs = (
                (True, [])
                if disable_posthoc_semantic_processing
                else _materialize_om2_quantities_from_hints(
                    ttl_path=published,
                    raw_hints=raw_hints if isinstance(raw_hints, list) else [],
                    ontology_contract=ontology_publish_contract,
                )
            )
            if om2_msgs:
                for msg in om2_msgs:
                    logger.info(f"    🌡️ {msg}")
            if not om2_ok:
                logger.error("    ❌ Published TTL OM-2 materialization failed:")
                for msg in om2_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1

            pruned, prune_msgs = (
                (True, [])
                if disable_posthoc_semantic_processing
                else _prune_unhinted_orphan_required_targets(
                    ttl_path=published,
                    raw_hints=raw_hints if isinstance(raw_hints, list) else [],
                    main_entity_policy=main_entity_policy,
                    ontology_contract=ontology_publish_contract,
                )
            )
            if prune_msgs:
                for msg in prune_msgs:
                    logger.info(f"    🧹 {msg}")
            if not pruned:
                logger.error("    ❌ Published TTL orphan pruning failed:")
                for msg in prune_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1

            hygiene_ok, hygiene_msgs = (
                (True, [])
                if disable_posthoc_semantic_processing
                else enforce_published_graph_hygiene_file(
                    ttl_path=published,
                    top_entity_uri=entity_uri,
                    top_class_iri=_ontology_contract_top_class_iri(
                        ontology_publish_contract
                    ),
                    entity_label=entity_label,
                )
            )
            if hygiene_msgs:
                for msg in hygiene_msgs:
                    logger.info(f"    🧼 {msg}")
            if not hygiene_ok:
                logger.error("    ❌ Published TTL graph hygiene enforcement failed:")
                for msg in hygiene_msgs:
                    logger.error(f"       - {msg}")
                publish_failures += 1

            ordered_ok, ordered_report = (
                (True, {"status": "skipped", "messages": []})
                if disable_posthoc_semantic_processing
                else enforce_ordered_member_integrity_file(
                    ttl_path=published,
                    runtime_profile=runtime_ordered_member_profile,
                    top_entity_uri=entity_uri,
                )
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

            hygiene_ok, hygiene_msgs = (
                (True, [])
                if disable_posthoc_semantic_processing
                else enforce_published_graph_hygiene_file(
                    ttl_path=published,
                    top_entity_uri=entity_uri,
                    top_class_iri=_ontology_contract_top_class_iri(
                        ontology_publish_contract
                    ),
                    entity_label=entity_label,
                )
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

            repair_contexts = info.get("repair_contexts") or []
            continuity_inputs: list[dict[str, Any]] = []
            if isinstance(repair_contexts, list):
                for context in repair_contexts:
                    if not isinstance(context, dict):
                        continue
                    try:
                        context_iteration = int(context.get("iteration"))
                    except (TypeError, ValueError):
                        continue
                    checkpoint_ttl, _ = _entity_iteration_checkpoint_paths(
                        doi_folder,
                        safe,
                        context_iteration,
                    )
                    if not os.path.isfile(checkpoint_ttl):
                        continue
                    continuity_inputs.append(
                        {
                            "iteration": context_iteration,
                            "hints_content": str(
                                context.get("hints_content") or ""
                            ),
                            "abox_path": checkpoint_ttl,
                        }
                    )
            continuity_cfg = _resolve_continuity_audit_policy(main_entity_policy)
            if continuity_inputs and continuity_cfg.get("enabled"):
                continuity_report = await asyncio.to_thread(
                    judge_iteration_continuity,
                    prior_iterations=continuity_inputs,
                    final_abox_path=Path(published),
                    ontology_contract=ontology_publish_contract,
                    model=agent_model,
                    reviewer_model=agent_model,
                    verifier_model=agent_model,
                )
                continuity_dir = os.path.join(
                    doi_folder,
                    "responses",
                    "iteration_continuity",
                )
                os.makedirs(_filesystem_path(continuity_dir), exist_ok=True)
                continuity_path = os.path.join(
                    continuity_dir,
                    f"{safe}.continuity_audit.json",
                )
                continuity_repair_attempts: list[dict[str, Any]] = []
                if not bool(continuity_report.get("accepted")):
                    try:
                        continuity_retry_limit = max(
                            0,
                            int(
                                main_entity_policy.get(
                                    "continuity_audit_retries", 2
                                )
                            ),
                        )
                    except (TypeError, ValueError):
                        continuity_retry_limit = 2
                    continuity_contexts = info.get("repair_contexts") or []
                    if not isinstance(continuity_contexts, list):
                        continuity_contexts = []
                    for repair_attempt in range(1, continuity_retry_limit + 1):
                        repair_context = _select_continuity_repair_context(
                            continuity_report=continuity_report,
                            contexts=continuity_contexts,
                        )
                        attempt_record: dict[str, Any] = {
                            "attempt": repair_attempt,
                            "status": "pending",
                            "repair_iteration": (
                                repair_context.get("iteration")
                                if repair_context is not None
                                else None
                            ),
                        }
                        if repair_context is None:
                            attempt_record["status"] = "no_repair_context"
                            continuity_repair_attempts.append(attempt_record)
                            break
                        published_backup = Path(published).read_bytes()
                        try:
                            logger.warning(
                                "    ♻️  Routing %d confirmed continuity regression(s) "
                                "to retained-memory repair (attempt %d/%d)",
                                len(
                                    continuity_report.get(
                                        "confirmed_regressions"
                                    )
                                    or []
                                ),
                                repair_attempt,
                                continuity_retry_limit,
                            )
                            await run_kg_building_agent(
                                doi_hash=doi_hash,
                                entity_label=entity_label,
                                entity_uri=entity_uri,
                                hints_content=str(
                                    repair_context.get("hints_content") or ""
                                ),
                                kg_prompt=_continuity_repair_prompt(
                                    continuity_report
                                ),
                                iter_num=int(repair_context.get("iteration")),
                                mcp_tools=list(
                                    repair_context.get("mcp_tools") or []
                                ),
                                mcp_set_name=str(
                                    repair_context.get("mcp_set_name") or ""
                                ),
                                data_dir=data_dir,
                                main_entity_policy=main_entity_policy,
                                ontology_contract=ontology_publish_contract,
                                agent_model=str(
                                    repair_context.get("agent_model")
                                    or agent_model
                                ),
                                entity_scope=safe,
                                known_top_entities=top_entities,
                                identity_dossier=dict(
                                    info.get("identity_dossier") or {}
                                ),
                                run_label=f"continuity_repair_{repair_attempt}",
                                compiled_iteration_spec=dict(
                                    repair_context.get(
                                        "compiled_iteration_spec"
                                    )
                                    or {}
                                ),
                            )
                            latest_artifact = _select_canonical_resume_artifact(
                                doi_folder=doi_folder,
                                entity_label=entity_label,
                                entity_safe=safe,
                                entity_uri=entity_uri,
                            )
                            if not latest_artifact:
                                raise RuntimeError(
                                    "Continuity repair did not persist an entity artifact"
                                )
                            if os.path.normcase(
                                os.path.abspath(latest_artifact)
                            ) != os.path.normcase(os.path.abspath(published)):
                                shutil.copy2(
                                    _filesystem_path(latest_artifact),
                                    _filesystem_path(published),
                                )
                            hygiene_ok, hygiene_msgs = (
                                enforce_published_graph_hygiene_file(
                                    ttl_path=published,
                                    top_entity_uri=entity_uri,
                                    top_class_iri=_ontology_contract_top_class_iri(
                                        ontology_publish_contract
                                    ),
                                    entity_label=entity_label,
                                )
                            )
                            if not hygiene_ok:
                                raise RuntimeError(
                                    "Continuity-repair graph hygiene failed: "
                                    + "; ".join(hygiene_msgs)
                                )
                            continuity_report = await asyncio.to_thread(
                                judge_iteration_continuity,
                                prior_iterations=continuity_inputs,
                                final_abox_path=Path(published),
                                ontology_contract=ontology_publish_contract,
                                model=agent_model,
                                reviewer_model=agent_model,
                                verifier_model=agent_model,
                            )
                            attempt_record["status"] = (
                                "resolved"
                                if bool(continuity_report.get("accepted"))
                                else "unresolved"
                            )
                            attempt_record["confirmed_regression_count"] = len(
                                continuity_report.get("confirmed_regressions")
                                or []
                            )
                            continuity_repair_attempts.append(attempt_record)
                            _write_json_atomic(
                                os.path.join(
                                    continuity_dir,
                                    f"{safe}.continuity_audit.attempt_{repair_attempt}.json",
                                ),
                                continuity_report,
                            )
                            if bool(continuity_report.get("accepted")):
                                logger.info(
                                    "    ✅ Continuity repair resolved all confirmed "
                                    "regressions on attempt %d",
                                    repair_attempt,
                                )
                                break
                        except Exception as exc:
                            Path(published).write_bytes(published_backup)
                            attempt_record["status"] = "repair_failed"
                            attempt_record["error"] = str(exc)
                            continuity_repair_attempts.append(attempt_record)
                            logger.warning(
                                "    ⚠️  Continuity repair attempt %d failed: %s",
                                repair_attempt,
                                exc,
                            )
                if continuity_repair_attempts:
                    continuity_report["repair_attempts"] = continuity_repair_attempts
                _write_json_atomic(continuity_path, continuity_report)
                uncertain_count = len(
                    continuity_report.get("uncertain_transitions") or []
                )
                if uncertain_count:
                    logger.warning(
                        "    ⚠️  Cross-iteration continuity audit retained %d "
                        "non-blocking uncertain transition(s)",
                        uncertain_count,
                    )
                if not bool(continuity_report.get("accepted")):
                    regressions = continuity_report.get(
                        "confirmed_regressions"
                    ) or []
                    if _semantic_audit_nonblocking(main_entity_policy):
                        continuity_report["accepted_after_audit_exhaustion"] = True
                        continuity_report["acceptance_override"] = {
                            "reason": "continuity_audit_budget_exhausted",
                            "audit_attempt_budget": (
                                len(continuity_repair_attempts) + 1
                            ),
                            "candidate_requirement": (
                                "published graph already passed deterministic "
                                "persistence and hygiene checks"
                            ),
                            "confirmed_regression_count": len(regressions),
                        }
                        _write_json_atomic(continuity_path, continuity_report)
                        logger.warning(
                            "    ⚠️  Cross-iteration continuity audit confirmed %d "
                            "regression(s) after the audit budget was exhausted; "
                            "preserving the deterministically valid published candidate. "
                            "Report: %s",
                            len(regressions),
                            os.path.relpath(continuity_path, doi_folder),
                        )
                    else:
                        logger.error(
                            "    ❌ Cross-iteration continuity audit confirmed %d "
                            "regression(s); refusing publication. Report: %s",
                            len(regressions),
                            os.path.relpath(continuity_path, doi_folder),
                        )
                        Path(published).unlink(missing_ok=True)
                        publish_failures += 1
                        continue
            elif continuity_inputs:
                logger.info(
                    "    Continuity audit disabled; skipping judge and repair"
                )

            logger.info(
                f"    ✅ Published entity TTL: {os.path.relpath(published, doi_folder)}"
            )
            ok_struct, struct_msgs = (
                (True, [])
                if disable_posthoc_semantic_processing
                else _validate_entity_ttl_structure(
                    ttl_path=published,
                    entity_uri=entity_uri,
                    entity_label=entity_label,
                    ontology_contract=ontology_publish_contract,
                    known_top_entity_uris=known_top_entity_uris,
                )
            )
            if not ok_struct:
                logger.error("    ❌ Published TTL failed structural validation:")
                for msg in struct_msgs:
                    logger.error(f"       - {msg}")
                repair_contexts = info.get("repair_contexts") or []
                if not isinstance(repair_contexts, list):
                    repair_contexts = []
                try:
                    structural_retry_limit = max(
                        0,
                        int(
                            main_entity_policy.get(
                                "post_publish_structural_retries", 2
                            )
                        ),
                    )
                except (TypeError, ValueError):
                    structural_retry_limit = 2
                feedback_attempt_start = _next_post_publish_feedback_attempt(
                    doi_folder=doi_folder,
                    entity_scope=safe,
                )
                for repair_attempt in range(1, structural_retry_limit + 1):
                    feedback_attempt = feedback_attempt_start + repair_attempt - 1
                    failed_messages = list(struct_msgs)
                    repair_context, required_links = (
                        _select_post_publish_repair_context(
                            messages=failed_messages,
                            ontology_contract=ontology_publish_contract,
                            contexts=repair_contexts,
                        )
                    )
                    feedback_path = _write_post_publish_feedback(
                        doi_folder=doi_folder,
                        entity_scope=safe,
                        entity_label=entity_label,
                        entity_uri=entity_uri,
                        attempt=feedback_attempt,
                        messages=failed_messages,
                        required_links=required_links,
                        repair_context=repair_context,
                        retry_status="pending",
                        retained_checkpoint_sha256=str(
                            info.get("resume_checkpoint_sha256") or ""
                        ),
                    )
                    logger.warning(
                        "    ♻️  Routing post-publish structural failure to the "
                        "retained-memory KG agent (attempt %d/%d); feedback=%s",
                        repair_attempt,
                        structural_retry_limit,
                        os.path.relpath(feedback_path, doi_folder),
                    )
                    if repair_context is None:
                        _write_post_publish_feedback(
                            doi_folder=doi_folder,
                            entity_scope=safe,
                            entity_label=entity_label,
                            entity_uri=entity_uri,
                            attempt=feedback_attempt,
                            messages=failed_messages,
                            required_links=required_links,
                            repair_context=None,
                            retry_status="no_repair_context",
                            post_retry_messages=failed_messages,
                            retained_checkpoint_sha256=str(
                                info.get("resume_checkpoint_sha256") or ""
                            ),
                        )
                        break
                    try:
                        repair_prompt = _post_publish_repair_prompt(
                            messages=failed_messages,
                            required_links=required_links,
                        )
                        await run_kg_building_agent(
                            doi_hash=doi_hash,
                            entity_label=entity_label,
                            entity_uri=entity_uri,
                            hints_content=str(
                                repair_context.get("hints_content") or ""
                            ),
                            kg_prompt=repair_prompt,
                            iter_num=int(repair_context.get("iteration")),
                            mcp_tools=list(repair_context.get("mcp_tools") or []),
                            mcp_set_name=str(
                                repair_context.get("mcp_set_name") or ""
                            ),
                            data_dir=data_dir,
                            main_entity_policy=main_entity_policy,
                            ontology_contract=ontology_publish_contract,
                            agent_model=str(
                                repair_context.get("agent_model") or agent_model
                            ),
                            entity_scope=safe,
                            known_top_entities=top_entities,
                            identity_dossier=dict(entity.get("identity_dossier") or {}),
                            run_label=f"post_publish_{feedback_attempt}",
                            compiled_iteration_spec=dict(
                                repair_context.get("compiled_iteration_spec") or {}
                            ),
                        )
                        latest_artifact = _select_canonical_resume_artifact(
                            doi_folder=doi_folder,
                            entity_label=entity_label,
                            entity_safe=safe,
                            entity_uri=entity_uri,
                        )
                        if not latest_artifact:
                            raise RuntimeError(
                                "KG repair agent did not leave a persisted entity artifact"
                            )
                        if os.path.normcase(os.path.abspath(latest_artifact)) != os.path.normcase(
                            os.path.abspath(published)
                        ):
                            shutil.copy2(
                                _filesystem_path(latest_artifact),
                                _filesystem_path(published),
                            )
                        hygiene_ok, hygiene_msgs = (
                            enforce_published_graph_hygiene_file(
                                ttl_path=published,
                                top_entity_uri=entity_uri,
                                top_class_iri=_ontology_contract_top_class_iri(
                                    ontology_publish_contract
                                ),
                                entity_label=entity_label,
                            )
                        )
                        if not hygiene_ok:
                            raise RuntimeError(
                                "Post-retry graph hygiene failed: "
                                + "; ".join(hygiene_msgs)
                            )
                        ok_struct, struct_msgs = _validate_entity_ttl_structure(
                            ttl_path=published,
                            entity_uri=entity_uri,
                            entity_label=entity_label,
                            ontology_contract=ontology_publish_contract,
                            known_top_entity_uris=known_top_entity_uris,
                        )
                        _write_post_publish_feedback(
                            doi_folder=doi_folder,
                            entity_scope=safe,
                            entity_label=entity_label,
                            entity_uri=entity_uri,
                            attempt=feedback_attempt,
                            messages=failed_messages,
                            required_links=required_links,
                            repair_context=repair_context,
                            retry_status="resolved" if ok_struct else "unresolved",
                            post_retry_messages=struct_msgs,
                            retained_checkpoint_sha256=str(
                                info.get("resume_checkpoint_sha256") or ""
                            ),
                        )
                        if ok_struct:
                            logger.info(
                                "    ✅ KG agent resolved post-publish structural "
                                "validation on attempt %d",
                                repair_attempt,
                            )
                            break
                        logger.error(
                            "    ❌ Post-publish structural retry %d remains invalid:",
                            repair_attempt,
                        )
                        for msg in struct_msgs:
                            logger.error(f"       - {msg}")
                    except Exception as exc:
                        struct_msgs = [str(exc)]
                        _write_post_publish_feedback(
                            doi_folder=doi_folder,
                            entity_scope=safe,
                            entity_label=entity_label,
                            entity_uri=entity_uri,
                            attempt=feedback_attempt,
                            messages=failed_messages,
                            required_links=required_links,
                            repair_context=repair_context,
                            retry_status="agent_retry_failed",
                            post_retry_messages=struct_msgs,
                            retained_checkpoint_sha256=str(
                                info.get("resume_checkpoint_sha256") or ""
                            ),
                        )
                        logger.error(
                            "    ❌ Post-publish KG repair attempt %d failed: %s",
                            repair_attempt,
                            exc,
                        )
                if not ok_struct:
                    publish_failures += 1
            if ok_struct:
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
    os.environ["TWA_CENTRAL_MEMORY_DIR"] = os.path.join(
        os.path.abspath(str(data_dir)), "central_memory"
    )

    logger.info(f"🏗️  Starting main KG building for DOI: {doi_hash}")

    doi_folder = os.path.join(data_dir, doi_hash)
    if not os.path.exists(doi_folder):
        logger.error(f"DOI folder not found: {doi_folder}")
        return False

    mcp_run_dir = os.path.join(doi_folder, "mcp_run")

    # Check if step is already completed
    marker_file = os.path.join(doi_folder, ".main_kg_building_done")
    selected_entity_safe = str(
        config.get("only_entity_safe")
        or config.get("_entity_first_entity_safe")
        or ""
    ).strip()
    if os.path.exists(marker_file) and not selected_entity_safe:
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
            publisher_code_mtime = 0.0
            try:
                publisher_path = publish_ttl.__code__.co_filename
                if publisher_path and os.path.exists(publisher_path):
                    publisher_code_mtime = os.path.getmtime(publisher_path)
            except Exception:
                publisher_code_mtime = 0.0
            latest_required_mtime = max(
                latest_hint_mtime,
                config_mtime,
                step_code_mtime,
                publisher_code_mtime,
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
                logger.info("  ⏭️  Main KG building already completed (marker exists)")
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
    if ontology_name == "medical":
        config = ensure_kg_norev(config, default=True)
    try:
        ontology_publish_contract = build_ontology_publish_contract(
            meta_task_config_path=meta_task_config_path,
            ontology_name=ontology_name or None,
        )
    except Exception as exc:
        logger.error("Failed to build ontology publish contract: %s", exc)
        return False
    top_class_iri = _ontology_contract_top_class_iri(ontology_publish_contract)
    selected_top_class_iri, _ = load_selected_top_class(doi_folder)
    if not selected_top_class_iri:
        logger.error("Pipeline-selected top class is missing")
        return False
    if top_class_iri and selected_top_class_iri != top_class_iri:
        logger.error(
            "Pipeline-selected top class disagrees with the active ontology contract"
        )
        return False
    try:
        top_entities = hydrate_and_validate_top_entity_types(
            entities=top_entities,
            iteration_1_ttl=os.path.join(doi_folder, "iteration_1.ttl"),
            top_class_iri=selected_top_class_iri,
        )
    except Exception as exc:
        logger.error("Failed to validate top-entity identity manifest: %s", exc)
        return False
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
        ontology_contract=ontology_publish_contract,
    )
    try:
        top_entities = hydrate_and_validate_top_entity_types(
            entities=top_entities,
            iteration_1_ttl=os.path.join(doi_folder, "iteration_1.ttl"),
            top_class_iri=selected_top_class_iri,
        )
        persist_entity_identity_sidecars(
            doi_hash=doi_hash,
            doi_folder=doi_folder,
            entities=top_entities,
            top_class_iri=selected_top_class_iri,
        )
    except Exception as exc:
        logger.error("Failed to persist top-entity identity sidecars: %s", exc)
        return False
    try:
        with open(entities_path, "w", encoding="utf-8") as f:
            json.dump(top_entities, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to persist canonicalized top entities: {e}")

    selected_entity_safe = str(
        config.get("only_entity_safe")
        or config.get("_entity_first_entity_safe")
        or ""
    ).strip()
    if selected_entity_safe:
        top_entities = [
            entity
            for entity in top_entities
            if _safe_name(str(entity.get("label") or "")) == selected_entity_safe
        ]
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
            logger.error("  ❌ Iteration processing failed")
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
        logger.info("  📌 Created completion marker")
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
