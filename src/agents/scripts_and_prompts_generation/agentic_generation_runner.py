from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
    build_contexts_for_ontologies,
)
from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    build_validation_report,
)
from src.agents.scripts_and_prompts_generation.artifact_state import (
    ArtifactStateStore,
)
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _owned_entity_tool_contracts,
    _prompt_tbox_slice,
    _subclass_decision_contract,
    _write_materializable_prompt_component,
    run_pure_llm_generation_rounds,
)
from src.agents.scripts_and_prompts_generation.reuse_policy import (
    existing_entity_check_contracts,
)


def _py_name(name: str) -> str:
    out = re.sub(r"\W+", "_", str(name or "")).strip("_")
    if not out:
        out = "unnamed"
    if out[:1].isdigit():
        out = "_" + out
    return out


def _local_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _resumable_artifact_snapshots(
    context: AgenticGenerationContext,
    requested_target_names: set[str],
) -> dict[Path, bytes]:
    """Capture journal-backed bytes before deterministic scaffolding runs."""
    artifact_state = ArtifactStateStore(
        context.output_root, context.ontology.name
    )
    snapshots: dict[Path, bytes] = {}
    for artifact_dir, suffix in (
        (Path(context.scripts_dir), ".py"),
        (Path(context.prompts_dir), ".md"),
    ):
        if not artifact_dir.is_dir():
            continue
        for artifact in artifact_dir.glob(f"*{suffix}"):
            if (
                (not requested_target_names or artifact.name in requested_target_names)
                and artifact_state.should_preserve_existing(artifact)
            ):
                snapshots[artifact] = artifact.read_bytes()
    return snapshots


def _configured_prompt_addon(context: AgenticGenerationContext) -> str:
    """Keep non-T-Box runtime configuration out of generated prompt semantics."""
    return ""


def _mutually_exclusive_property_groups(
    context: AgenticGenerationContext,
) -> list[dict[str, Any]]:
    return []


def _format_mutually_exclusive_property_contract(
    context: AgenticGenerationContext,
) -> str:
    groups = _mutually_exclusive_property_groups(context)
    if not groups:
        return ""
    lines = [
        "Mutually Exclusive Property Contract:",
        "- Treat each configured group as an ontology-derived integrity rule. For one entity instance, emit at most one active value from the listed datatype properties.",
        '- Values such as empty string, `"-"`, `"0"`, `false`, `False`, `no`, or `none` are inactive; any other source-supported value is active.',
        "- If the source supports multiple candidates in one group, choose the single best-supported property according to the T-Box comments and local source evidence; omit the rest.",
        "- Do not emit a plausible default for a mutually exclusive property when source evidence only supports a different property in the same group.",
    ]
    for group in groups:
        props = ", ".join(f"`{prop}`" for prop in group["properties"])
        lines.append(
            f"- `{group['target_class']}`: at most one active value among {props}."
        )
    return "\n".join(lines) + "\n"


def _namespace_uri(context: AgenticGenerationContext) -> str:
    ns = str(context.contract.get("namespace_uri") or "").strip()
    if ns:
        return ns
    classes = context.parsed.get("classes") or {}
    for cls in classes.values():
        iri = str((cls or {}).get("iri") or "")
        if iri:
            return iri.rstrip("/#").rsplit("/", 1)[0] + "/"
    return "https://www.theworldavatar.com/kg/generated/"


def _class_ancestors(classes: dict[str, Any], class_name: str) -> list[str]:
    """Return T-Box parent classes, nearest first, without hardcoded ontology names."""
    out: list[str] = []
    queue = list(((classes.get(class_name) or {}).get("parent_classes") or []))
    while queue:
        parent = str(queue.pop(0) or "").strip()
        if not parent or parent in out:
            continue
        out.append(parent)
        queue.extend((classes.get(parent) or {}).get("parent_classes") or [])
    return out


def _normalized_symbol(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _predicate_target_stem(predicate_local: str) -> str:
    text = str(predicate_local or "").strip()
    for prefix in ("has", "is"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :]
    return text


def _ordered_member_classes(context: AgenticGenerationContext) -> set[str]:
    profile = context.contract.get("ordered_member_profile") or {}
    return {
        str(x).strip()
        for x in profile.get("ordered_member_classes", []) or []
        if str(x).strip()
    }


def _ordering_datatype_properties(context: AgenticGenerationContext) -> set[str]:
    profile = context.contract.get("ordered_member_profile") or {}
    return {
        str(x).strip()
        for x in profile.get("single_valued_ordering_properties", []) or []
        if str(x).strip()
    }


def _step_scoped_object_properties_for_class(
    context: AgenticGenerationContext, class_local: str
) -> dict[str, str]:
    """Return T-Box object properties that may be attached to this ordered-member class."""
    props: dict[str, str] = {}
    classes = context.parsed.get("classes") or {}
    class_family = {class_local, *_class_ancestors(classes, class_local)}

    for spec in context.contract.get("step_scoped_object_properties") or []:
        domain = str((spec or {}).get("domain_local") or "").strip()
        prop = str((spec or {}).get("predicate_local") or "").strip()
        range_local = str((spec or {}).get("range_local") or "").strip()
        if domain in class_family and prop and range_local:
            props[prop] = range_local

    property_defs = context.parsed.get("properties") or {}
    for prop, spec in (
        context.contract.get("relationship_domain_contracts") or {}
    ).items():
        prop_local = str(prop or "").strip()
        if not prop_local:
            continue
        members = {
            _local_name(member)
            for member in ((spec or {}).get("union_members") or [])
            if str(member or "").strip()
        }
        preferred = str((spec or {}).get("preferred_domain_local") or "").strip()
        if class_local not in members and preferred not in class_family:
            continue
        range_local = str(
            ((property_defs.get(prop_local) or {}).get("range") or "")
        ).strip()
        if range_local:
            props[prop_local] = range_local
    return props


def _base_script(context: AgenticGenerationContext) -> str:
    ns = _namespace_uri(context)
    memory_dir_name = (
        f"memory_{_py_name(context.ontology.name)}"
        if context.ontology.role == "extension"
        else "memory"
    )
    ordering_properties = sorted(_ordering_datatype_properties(context))
    predicate_uris = {
        str(prop_local): str((prop or {}).get("iri") or "")
        for prop_local, prop in (context.parsed.get("properties") or {}).items()
        if str(prop_local).strip() and str((prop or {}).get("iri") or "").strip()
    }
    reusable_class_iris = [
        str(item["class_iri"])
        for item in existing_entity_check_contracts(
            parsed=context.parsed,
            contract=context.contract,
        )
        if item.get("reuse_authorized") is True
    ]
    top_local = str(
        (context.contract.get("top_entity") or {}).get("class_local") or ""
    ).strip()
    required_helpers = """

def _read_global_state():
    return ("", "top", str(NS["top"]))


def _ensure_required_top_links_before_export() -> None:
    # Validation hook: concrete link repair is generated in later iterations.
    return None
"""
    return f"""from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import XSD
from . import _fixed_rdf_runtime as rdf_runtime


NS = Namespace({ns!r})
OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")
PREDICATE_URIS = {predicate_uris!r}
ORDERING_PROPERTY_LOCALS = {ordering_properties!r}
REUSABLE_CLASS_IRIS = {reusable_class_iris!r}
GRAPH = rdf_runtime.retained_graph()
GRAPH.bind("om-2", OM2)
CURRENT_DOI = ""
CURRENT_DOI_RAW = ""
CURRENT_ENTITY_CONTEXT = "top"


def _format_success_json(
    iri,
    message: str,
    *,
    created: bool,
    **metadata,
) -> str:
    return json.dumps({{
        "status": "ok",
        "iri": str(iri),
        "message": message,
        "created": created,
        **metadata,
    }})


def _format_error_json(code: str, message: str) -> str:
    return json.dumps({{"status": "error", "code": code, "message": message}})


def _safe_filename_component(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "").strip())
    chars = []
    for char in normalized:
        if ord(char) < 128:
            chars.append(char)
            continue
        try:
            char_name = unicodedata.name(char)
        except ValueError:
            chars.append("_")
            continue
        if char_name.startswith("GREEK ") and " LETTER " in char_name:
            chars.append(char_name.rsplit(" LETTER ", 1)[-1].lower())
        else:
            chars.append("_")
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", "".join(chars)).strip("._")
    return text or "entity"


def _data_root() -> Path:
    return Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data")


def _resolve_case_dirname(doi_value: str) -> str:
    # Map a document id (hash or DOI) onto the pipeline case folder name.
    raw = str(doi_value or "").strip() or "unknown"
    safe = _safe_filename_component(raw)
    root = _data_root()
    mapping_path = root / "doi_to_hash.json"
    if not mapping_path.exists():
        return safe
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    except Exception:
        return safe
    hashes = {{str(v).strip() for v in mapping.values() if str(v).strip()}}
    if safe in hashes:
        return safe
    candidates = {{
        raw,
        safe,
        raw.replace("_", "/"),
        raw.replace("/", "_"),
        safe.replace("_", "/"),
    }}
    for doi_key, hash_value in mapping.items():
        key = str(doi_key or "").strip()
        hashed = str(hash_value or "").strip()
        if not key or not hashed:
            continue
        key_us = key.replace("/", "_")
        if key in candidates or key_us in candidates or _safe_filename_component(key_us) == safe:
            return hashed
    return safe


def _memory_paths(doi: str | None = None, entity_context: str | None = None) -> tuple[Path, Path]:
    doi_value = str(doi or CURRENT_DOI or "unknown").strip() or "unknown"
    entity_value = str(entity_context or CURRENT_ENTITY_CONTEXT or "top").strip() or "top"
    doi_dir = _data_root() / _resolve_case_dirname(doi_value)
    memory_dir = doi_dir / {memory_dir_name!r}
    exports_dir = doi_dir / "exports"
    memory_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    safe_entity = _safe_filename_component(entity_value)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return memory_dir / f"{{safe_entity}}.ttl", exports_dir / f"{{safe_entity}}_{{timestamp}}.ttl"


def init_memory_wrapper(
    doi: str,
    top_level_entity_name: str = "top",
) -> str:
    global CURRENT_DOI, CURRENT_DOI_RAW, CURRENT_ENTITY_CONTEXT
    CURRENT_DOI_RAW = str(doi or "").strip()
    CURRENT_DOI = _resolve_case_dirname(CURRENT_DOI_RAW)
    CURRENT_ENTITY_CONTEXT = str(top_level_entity_name or "top").strip() or "top"
    memory_ttl, _ = _memory_paths(CURRENT_DOI, CURRENT_ENTITY_CONTEXT)
    if memory_ttl.exists() and memory_ttl.stat().st_size > 0:
        GRAPH.parse(memory_ttl, format="turtle")
    return json.dumps({{
        "status": "ok",
        "doi": CURRENT_DOI,
        "top_level_entity_name": CURRENT_ENTITY_CONTEXT,
        "mode": "open_or_resume",
        "total_triples": len(GRAPH),
    }})


def get_top_entity_iri(label: str | None = None) -> str:
    scoped_label = str(label or CURRENT_ENTITY_CONTEXT or "top").strip() or "top"
    return str(NS[_safe_filename_component(scoped_label)])


TOP_ENTITY_CLASS_LOCAL = {top_local!r}
GENERIC_TOP_ENTITY_LABELS = {{"", "top"}}
if TOP_ENTITY_CLASS_LOCAL:
    GENERIC_TOP_ENTITY_LABELS.add(TOP_ENTITY_CLASS_LOCAL.lower())


def _is_generic_top_entity_label(label: str) -> bool:
    text = str(label or "").strip()
    if not text:
        return True
    if text.lower() in GENERIC_TOP_ENTITY_LABELS:
        return True
    return bool(TOP_ENTITY_CLASS_LOCAL and re.fullmatch(rf"{{re.escape(TOP_ENTITY_CLASS_LOCAL)}}-\\d+", text))


def _mint_hash_iri(prefix: str, label: str) -> URIRef:
    # Mint non-top individuals within the active scoped top-entity context so
    # same-label targets from different syntheses do not collapse after merge.
    safe = str(abs(hash((CURRENT_ENTITY_CONTEXT, prefix, label))))
    return URIRef(NS[f"{{prefix}}_{{safe}}"])


def _find_by_type_and_label(class_iri, label: str):
    label_text = str(label or "").strip()
    candidates = [label_text]
    context_suffix = str(CURRENT_ENTITY_CONTEXT or "").strip()
    if context_suffix:
        suffix_variants = {{
            context_suffix,
            context_suffix.replace("–", "-").replace("—", "-"),
            _safe_filename_component(context_suffix),
        }}
        for suffix_value in [x for x in suffix_variants if x]:
            for suffix in ("_" + suffix_value, " " + suffix_value, "-" + suffix_value):
                if label_text.endswith(suffix):
                    stripped = label_text[: -len(suffix)].strip(" _-")
                    if stripped and stripped not in candidates:
                        candidates.append(stripped)
        dash_normalized = label_text.replace("–", "-").replace("—", "-")
        if dash_normalized != label_text and dash_normalized not in candidates:
            candidates.append(dash_normalized)
        for suffix_value in [x.replace("–", "-").replace("—", "-") for x in suffix_variants if x]:
            for suffix in ("_" + suffix_value, " " + suffix_value, "-" + suffix_value):
                if dash_normalized.endswith(suffix):
                    stripped = dash_normalized[: -len(suffix)].strip(" _-")
                    if stripped and stripped not in candidates:
                        candidates.append(stripped)
    for candidate in candidates:
        for subject in GRAPH.subjects(RDF.type, class_iri):
            if candidate and (subject, RDFS.label, Literal(candidate)) in GRAPH:
                return subject
    return None


def _create_entity(class_local: str, label: str, *, prefer_top: bool = False):
    label_text = str(label or class_local).strip()
    if prefer_top and _is_generic_top_entity_label(label_text):
        raise ValueError(
            f"Refusing to create generic top entity label {{label_text!r}}; pass the source-supported entity label instead"
        )
    class_iri = NS[class_local]
    existing = _find_by_type_and_label(class_iri, label_text)
    if existing is not None:
        return existing, False
    iri = URIRef(get_top_entity_iri(label_text)) if prefer_top else _mint_hash_iri(class_local, label_text)
    GRAPH.add((iri, RDF.type, class_iri))
    GRAPH.add((iri, RDFS.label, Literal(label_text)))
    return iri, True


def _add_literal(subject_iri: str, predicate_local: str, value) -> None:
    if value is not None and str(value) != "":
        predicate = URIRef(PREDICATE_URIS.get(predicate_local) or str(NS[predicate_local]))
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item is not None and str(item) != "":
                if predicate_local in ORDERING_PROPERTY_LOCALS:
                    order_value = _coerce_positive_integer_order(predicate_local, item)
                    GRAPH.add((URIRef(subject_iri), predicate, Literal(order_value, datatype=XSD.integer)))
                else:
                    GRAPH.add((URIRef(subject_iri), predicate, Literal(item)))


def _coerce_positive_integer_order(predicate_local: str, value) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{{predicate_local}} must be a positive integer starting at 1; got {{value!r}}")
    if isinstance(value, int):
        order = value
    elif isinstance(value, float) and value.is_integer():
        order = int(value)
    else:
        text = str(value or "").strip()
        if re.fullmatch(r"\\d+", text):
            order = int(text)
        elif re.fullmatch(r"\\d+\\.0+", text):
            order = int(float(text))
        else:
            raise ValueError(f"{{predicate_local}} must be a positive integer starting at 1; got {{value!r}}")
    if order < 1:
        raise ValueError(f"{{predicate_local}} must be a positive integer starting at 1; got {{value!r}}")
    return order


def _add_object(subject_iri: str, predicate_local: str, object_iri: str) -> bool:
    predicate = URIRef(PREDICATE_URIS.get(predicate_local) or str(NS[predicate_local]))
    triple = (URIRef(subject_iri), predicate, URIRef(object_iri))
    created = triple not in GRAPH
    GRAPH.add(triple)
    return created


def export_memory_wrapper() -> str:
    _ensure_required_top_links_before_export()
    ttl = rdf_runtime.serialize_turtle(rdf_runtime.abox_graph(GRAPH))
    memory_ttl, export_ttl = _memory_paths()
    memory_ttl.write_text(ttl, encoding="utf-8")
    export_ttl.write_text(ttl, encoding="utf-8")
    return ttl
{required_helpers if required_helpers else ""}
"""


def _checks_script(context: AgenticGenerationContext) -> str:
    check_contracts = existing_entity_check_contracts(
        parsed=context.parsed,
        contract=context.contract,
    )
    class_funcs = []
    manifest = ["check_ordered_members"]
    for spec in check_contracts:
        tool_name = str(spec["public_tool"])
        manifest.append(tool_name)
        class_funcs.append(
            f"""def {tool_name}(
    proposed_entity_json: str = "",
    *,
    label: str = "",
) -> str:
    if not proposed_entity_json and label:
        proposed_entity_json = json.dumps({{"label": label}}, ensure_ascii=False)
    return _check_existing(
        class_local={str(spec["class_local"])!r},
        class_iri={str(spec["class_iri"])!r},
        lookup_scope={str(spec["lookup_scope"])!r},
        reuse_authorized={bool(spec["reuse_authorized"])!r},
        reference_resolution_only={bool(spec["reference_resolution_only"])!r},
        reuse_scope={str(spec["reuse_scope"])!r},
        match_basis={str(spec["match_basis"])!r},
        class_contract={{
            "comment": {str(spec.get("class_comment") or "")!r},
            "datatype_properties": {dict(spec.get("datatype_properties") or {})!r},
            "object_properties": {dict(spec.get("object_properties") or {})!r},
        }},
        proposed_entity_json=proposed_entity_json,
    )
"""
        )
    return (
        """from __future__ import annotations

import json
from rdflib import BNode, Graph, Literal, RDF, RDFS, URIRef

from ._fixed_rdf_runtime import (
    current_memory_scope,
    load_central_reuse_memory,
    load_document_reuse_memory,
    register_central_reuse_authorization,
    retained_graph,
)
from ._reuse_pair_judge import judge_reuse_pairs

SKOS_PREF_LABEL = URIRef("http://www.w3.org/2004/02/skos/core#prefLabel")
GRAPH = Graph()
PROVENANCE = {{}}


def _labels(node) -> list[str]:
    values = {{
        str(value)
        for predicate in (RDFS.label, SKOS_PREF_LABEL)
        for value in GRAPH.objects(node, predicate)
        if isinstance(value, Literal) and str(value).strip()
    }}
    return sorted(values)


def _types(node) -> list[str]:
    return sorted(
        {{
            str(value)
            for value in GRAPH.objects(node, RDF.type)
            if isinstance(value, URIRef)
        }}
    )


def _literal_detail(value: Literal) -> dict:
    return {{
        "value": str(value),
        "datatype": str(value.datatype) if value.datatype else None,
        "language": value.language,
    }}


def _related_detail(node) -> dict:
    return {{
        "iri": str(node),
        "labels": _labels(node),
        "types": _types(node),
    }}


def _instance_detail(node: URIRef) -> dict:
    datatype_values: dict[str, list[dict]] = {{}}
    outgoing_relations: dict[str, list[dict]] = {{}}
    incoming_relations: dict[str, list[dict]] = {{}}
    for predicate, value in GRAPH.predicate_objects(node):
        if predicate == RDF.type or predicate in (RDFS.label, SKOS_PREF_LABEL):
            continue
        if isinstance(value, Literal):
            datatype_values.setdefault(str(predicate), []).append(
                _literal_detail(value)
            )
        elif isinstance(value, (URIRef, BNode)):
            outgoing_relations.setdefault(str(predicate), []).append(
                _related_detail(value)
            )
    for subject, predicate in GRAPH.subject_predicates(node):
        if isinstance(subject, (URIRef, BNode)):
            incoming_relations.setdefault(str(predicate), []).append(
                _related_detail(subject)
            )
    return {{
        "iri": str(node),
        "labels": _labels(node),
        "types": _types(node),
        "datatype_values": datatype_values,
        "outgoing_relations": outgoing_relations,
        "incoming_relations": incoming_relations,
        "central_provenance": PROVENANCE.get(str(node), []),
    }}


def _check_existing(
    *,
    class_local: str,
    class_iri: str,
    lookup_scope: str,
    reuse_authorized: bool,
    reference_resolution_only: bool,
    reuse_scope: str,
    match_basis: str,
    class_contract: dict,
    proposed_entity_json: str,
) -> str:
    global GRAPH, PROVENANCE
    if lookup_scope == "central":
        GRAPH, PROVENANCE = load_central_reuse_memory({ontology_name!r})
    elif lookup_scope == "document":
        GRAPH, PROVENANCE = load_document_reuse_memory({ontology_name!r})
    else:
        GRAPH, PROVENANCE = retained_graph(), {{}}
    cls = URIRef(class_iri)
    instances = sorted(
        {{
            subject
            for subject in GRAPH.subjects(RDF.type, cls)
            if isinstance(subject, URIRef)
        }},
        key=str,
    )
    details = [_instance_detail(node) for node in instances]
    if lookup_scope in {{"central", "document"}}:
        try:
            proposed = json.loads(proposed_entity_json)
        except (TypeError, json.JSONDecodeError):
            proposed = None
        if not isinstance(proposed, dict) or not proposed:
            return json.dumps(
                {{
                    "status": "rejected",
                    "code": "PROPOSED_ENTITY_EVIDENCE_REQUIRED",
                    "class": class_local,
                    "instances": [],
                }},
                ensure_ascii=False,
                sort_keys=True,
            )
        scope = current_memory_scope()
        requests = [
            {{
                "pair_id": f"p{{index:04d}}",
                "class_iri": class_iri,
                "class_local": class_local,
                "class_contract": class_contract,
                "reuse_policy": {{
                    "reuse_scope": reuse_scope,
                    "match_basis": match_basis,
                }},
                "current_context": scope,
                "proposed_entity": proposed,
                "candidate_entity": detail,
            }}
            for index, detail in enumerate(details, start=1)
        ]
        try:
            judgements = judge_reuse_pairs(requests)
        except Exception as exc:
            return json.dumps(
                {{
                    "status": "rejected",
                    "code": "REUSE_JUDGE_FAILED_CLOSED",
                    "class": class_local,
                    "message": f"{{type(exc).__name__}}: {{exc}}",
                    "instances": [],
                }},
                ensure_ascii=False,
                sort_keys=True,
            )
        authorized = []
        for request, detail, judgement in zip(requests, details, judgements):
            if judgement.get("reuse_authorized") is not True:
                continue
            token = register_central_reuse_authorization(
                candidate_iri=detail["iri"],
                pair_id=request["pair_id"],
                judgement=judgement,
            )
            authorized.append(
                {{
                    **detail,
                    "reuse_authorization_token": token,
                    "reuse_judgement": judgement,
                }}
            )
        details = authorized
    return json.dumps(
        {{
            "status": "ok",
            "class": class_local,
            "class_iri": class_iri,
            "lookup_scope": lookup_scope,
            "class_reuse_eligible": reuse_authorized,
            "reuse_authorized": bool(details) if lookup_scope in {{"central", "document"}} else False,
            "reference_resolution_only": reference_resolution_only,
            "reuse_scope": reuse_scope,
            "match_basis": match_basis,
            "instances": details,
        }},
        ensure_ascii=False,
        sort_keys=True,
    )

def check_ordered_members() -> str:
    return json.dumps(
        {{
            "status": "error",
            "ok": False,
            "code": "ORDERED_MEMBER_CHECK_NOT_SESSION_BOUND",
            "message": (
                "This legacy creation-check module has no bound ledger. "
                "Use the occurrence MCP inspect_ordered_members tool, whose "
                "implementation is compiled with the occurrence contracts."
            ),
            "violations": [],
            "retryable": False,
            "skippable": False,
        }},
        ensure_ascii=False,
        sort_keys=True,
    )

__all__ = {manifest!r}

""".format(
            ontology_name=context.ontology.name,
            manifest=manifest,
        )
        + "\n".join(class_funcs)
    )


def _entities_script(context: AgenticGenerationContext) -> str:
    if (
        (
            context.contract.get("materialization_operation_units") or {}
        ).get("inference_mode")
        != "accepted_atomic"
    ):
        from src.agents.scripts_and_prompts_generation.legacy_script_generation import (
            legacy_entities_script,
        )

        return legacy_entities_script(context)
    has_om2_quantity = bool(context.contract.get("om2_quantity_properties"))
    creator_contracts = _owned_entity_tool_contracts(context)
    parts = [
        """from __future__ import annotations

import re

from . import _fixed_rdf_runtime as rdf_runtime
"""
    ]
    if has_om2_quantity:
        parts.append(
            "from ._fixed_rdf_runtime import create_om2_quantity\n\n"
        )
    parts.append(
        """_ENTITY_CREATORS = rdf_runtime.package_entity_capabilities()
_ORDERED_CREATORS = rdf_runtime.package_ordered_entity_capabilities()
_DATATYPE_WRITERS = rdf_runtime.package_datatype_capabilities()
_RELATIONSHIP_WRITERS = rdf_runtime.package_relationship_capabilities()


def _validate_label(value: object, field: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return rdf_runtime.error_json(code="INVALID_LABEL", message=f"{field} must be a non-empty string")
    return None


def _validate_iri(value: object, field: str) -> str | None:
    if not isinstance(value, str) or re.match(r"^https?://", value.strip()) is None:
        return rdf_runtime.error_json(code="INVALID_IRI", message=f"{field} must be an absolute HTTP(S) IRI")
    return None


def _validate_scalar(value: object, expected: str, field: str, required: bool = False) -> str | None:
    if value is None:
        return rdf_runtime.error_json(code="MISSING_REQUIRED_INPUT", message=f"{field} is required") if required else None
    valid = (
        isinstance(value, str) if expected == "str" else
        isinstance(value, bool) if expected == "bool" else
        isinstance(value, int) and not isinstance(value, bool) if expected == "int" else
        isinstance(value, (int, float)) and not isinstance(value, bool) if expected == "float" else
        False
    )
    if not valid:
        return rdf_runtime.error_json(code="INVALID_DATATYPE", message=f"{field} must be {expected}")
    return None


"""
    )
    entity_manifest: list[str] = []
    for creator in creator_contracts:
        tool_name = str(creator.get("public_tool") or "").strip()
        class_iri = str(creator.get("class_iri") or "").strip()
        class_local = str(creator.get("class_local") or "").strip()
        if not tool_name or not class_iri:
            continue
        entity_manifest.append(tool_name)
        ordering_local = str(creator.get("ordering_property_local") or "")
        required_params: list[tuple[str, str]] = []
        optional_params: list[tuple[str, str]] = []
        validation_lines = [
            "    error = _validate_label(label, \"label\")",
            "    if error: return error",
        ]
        owner_writer_lines: list[str] = []
        for datatype in creator.get("datatype_inputs") or []:
            property_local = str(datatype.get("property_local") or "")
            parameter_name = _py_name(property_local)
            python_type = str(datatype.get("python_type") or "str")
            required = bool(datatype.get("required"))
            (required_params if required else optional_params).append(
                (parameter_name, python_type)
            )
            validation_lines.extend(
                [
                    f"    error = _validate_scalar({parameter_name}, {python_type!r}, {parameter_name!r}, required={required!r})",
                    "    if error: return error",
                ]
            )
            if property_local != ordering_local:
                owner_writer_lines.append(
                    f"            if {parameter_name} is not None: _DATATYPE_WRITERS[{str(datatype.get('property_iri') or '')!r}](iri, {parameter_name})"
                )
        edge_writer_lines: list[str] = []
        dependent_metadata: list[str] = []
        for edge_index, edge in enumerate(creator.get("required_edges") or []):
            predicate_iri = str(edge.get("predicate_iri") or "")
            if edge.get("target_resolution") == "existing_iri_parameter":
                parameter_name = str(edge.get("parameter_name") or "")
                required_params.append((parameter_name, "str"))
                validation_lines.extend(
                    [
                        f"    error = _validate_iri({parameter_name}, {parameter_name!r})",
                        "    if error: return error",
                    ]
                )
                if edge.get("direction") == "container_as_subject_owner_as_object":
                    edge_writer_lines.append(
                        f"            _RELATIONSHIP_WRITERS[{predicate_iri!r}]({parameter_name}, iri)"
                    )
                else:
                    edge_writer_lines.append(
                        f"            _RELATIONSHIP_WRITERS[{predicate_iri!r}](iri, {parameter_name})"
                    )
            elif edge.get("target_resolution") == "same_operation_create":
                label_parameter = str(edge.get("label_parameter") or "")
                required_params.append((label_parameter, "str"))
                validation_lines.extend(
                    [
                        f"    error = _validate_label({label_parameter}, {label_parameter!r})",
                        "    if error: return error",
                    ]
                )
                dependent_name = f"dependent_iri_{edge_index}"
                edge_writer_lines.append(
                    f"            {dependent_name} = _ENTITY_CREATORS[{str(edge.get('dependent_fixed_capability_key') or '')!r}]({label_parameter})"
                )
                for dependent_input in edge.get("datatype_inputs") or []:
                    parameter_name = str(dependent_input.get("parameter_name") or "")
                    python_type = str(dependent_input.get("python_type") or "str")
                    required = bool(dependent_input.get("required"))
                    (required_params if required else optional_params).append(
                        (parameter_name, python_type)
                    )
                    validation_lines.extend(
                        [
                            f"    error = _validate_scalar({parameter_name}, {python_type!r}, {parameter_name!r}, required={required!r})",
                            "    if error: return error",
                        ]
                    )
                    edge_writer_lines.append(
                        f"            if {parameter_name} is not None: _DATATYPE_WRITERS[{str(dependent_input.get('property_iri') or '')!r}]({dependent_name}, {parameter_name})"
                    )
                edge_writer_lines.append(
                    f"            _RELATIONSHIP_WRITERS[{predicate_iri!r}](iri, {dependent_name})"
                )
                dependent_metadata.append(dependent_name)
        unique_required = list(dict.fromkeys(required_params))
        unique_optional = [
            item for item in dict.fromkeys(optional_params) if item not in unique_required
        ]
        signature_parts = ["label: str"]
        signature_parts.extend(f"{name}: {kind}" for name, kind in unique_required)
        signature_parts.extend(
            f"{name}: {kind} | None = None" for name, kind in unique_optional
        )
        ordering_parameter = _py_name(ordering_local)
        creator_line = (
            f"            iri = _ORDERED_CREATORS[{class_iri!r}](label, {ordering_parameter})"
            if creator.get("ordered_member")
            else f"            iri = _ENTITY_CREATORS[{class_iri!r}](label)"
        )
        mutation_lines = [
            "    try:",
            "        with rdf_runtime.atomic_graph_transaction():",
            creator_line,
            *owner_writer_lines,
            *edge_writer_lines,
            "    except Exception as exc:",
            "        return rdf_runtime.error_json(code=\"ATOMIC_CREATE_REJECTED\", message=str(exc))",
        ]
        metadata = (
            f", dependent_iris=[{', '.join(dependent_metadata)}]"
            if dependent_metadata
            else ""
        )
        parts.append(
            f"""def {tool_name}({", ".join(signature_parts)}) -> str:
{chr(10).join(validation_lines)}
{chr(10).join(mutation_lines)}
    return rdf_runtime.success_json(iri=iri, message={f"{class_local} created"!r}{metadata})


"""
        )
    if has_om2_quantity:
        entity_manifest.append("create_om2_quantity")
    parts.append(f"\n__all__ = {entity_manifest!r}\n")
    return "".join(parts)


def _relationships_script(context: AgenticGenerationContext) -> str:
    from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
        standalone_relationship_tool_contracts,
    )

    relationship_contracts = standalone_relationship_tool_contracts(
        context.contract.get("relationship_tool_contracts") or {},
        context.contract.get("materialization_operation_units") or {},
    )
    props = relationship_contracts or {
        name: {"predicate_local": name}
        for name, prop in (context.parsed.get("properties") or {}).items()
        if (prop or {}).get("kind") == "object"
    }
    parts = [
        f"""import re
from typing import Annotated
from pydantic import Field
from ._fixed_rdf_runtime import package_relationship_capabilities
from .{_py_name(context.ontology.name)}_creation_base import _format_error_json, _format_success_json

_RELATIONSHIP_CAPABILITIES = package_relationship_capabilities()

"""
    ]
    for prop in sorted(props.keys()):
        fn = _py_name(prop)
        relationship_contract = props.get(prop) or {}
        predicate_iri = str(relationship_contract.get("predicate_iri") or "").strip()
        range_locals = [
            str(value)
            for value in relationship_contract.get("range_locals") or []
            if str(value).strip()
        ]
        creator_tools = [
            str(value)
            for value in relationship_contract.get("creator_tools") or []
            if str(value).strip()
        ]
        external_range_iris = [
            str(value)
            for value in relationship_contract.get("external_range_iris") or []
            if str(value).strip()
        ]
        domain_locals = [
            _local_name(value)
            for value in relationship_contract.get("domain_iris") or []
            if _local_name(value)
        ]
        subject_desc = (
            "subject_iri must be an absolute IRI for domain "
            + (", ".join(domain_locals) or "T-Box-declared subject")
            + "; never a label/name/literal/plain text."
        )
        range_text = ", ".join(range_locals) or "T-Box-declared target"
        desc = (
            f"object_iri must be an absolute IRI for range {range_text}; "
            "never a label/name/literal/plain text."
        )
        if creator_tools:
            creator_text = ", ".join(creator_tools)
            desc += (
                f" For generated targets, use {creator_text}; object_iri must be the subject IRI "
                "returned by a successful creator call."
            )
        if external_range_iris and not relationship_contract.get(
            "external_creator_specs"
        ):
            desc += (
                " For external targets, pass an existing absolute IRI from the declared range; "
                "do not invent a creator tool."
            )
        doc = f'"""Add {prop}. {desc}"""'
        parts.append(
            f"""def add_{fn}(subject_iri: Annotated[str, Field(description={subject_desc!r})], object_iri: Annotated[str, Field(description={desc!r})], reuse_authorization_token: str | None = None) -> str:
    {doc}
    if not subject_iri:
        return _format_error_json("INVALID_SUBJECT_IRI", "subject_iri is required")
    if not object_iri:
        return _format_error_json("INVALID_OBJECT_IRI", "object_iri is required")
    if not re.match(r"^https?://", str(subject_iri or "")):
        return _format_error_json("INVALID_SUBJECT_IRI", "subject_iri must be an absolute IRI (http/https)")
    if not re.match(r"^https?://", str(object_iri or "")):
        return _format_error_json(
            "INVALID_OBJECT_IRI",
            "object_iri must be an absolute IRI (http/https), never a label/name/literal/plain text",
        )
    try:
        result = _RELATIONSHIP_CAPABILITIES[{predicate_iri!r}](
            subject_iri,
            object_iri,
            reuse_authorization_token,
        )
    except (KeyError, ValueError) as exc:
        return _format_error_json("RELATIONSHIP_CONTRACT_REJECTED", str(exc))
    return _format_success_json(object_iri, f"linked {prop}", created=True, enforcement=result)

"""
        )
    parts.append(
        "\n__all__ = "
        + repr([f"add_{_py_name(prop)}" for prop in sorted(props.keys())])
        + "\n"
    )
    return "".join(parts)


def _main_script(context: AgenticGenerationContext) -> str:
    from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
        standalone_relationship_tool_contracts,
    )

    creator_contracts = _owned_entity_tool_contracts(context)
    classes = sorted(
        str(item.get("class_local") or "")
        for item in creator_contracts
        if not item.get("external_range_class")
    )
    check_contracts = existing_entity_check_contracts(
        parsed=context.parsed,
        contract=context.contract,
    )
    check_tools = [str(spec["public_tool"]) for spec in check_contracts]
    external_creator_tools = [
        str((spec or {}).get("public_tool") or "").strip()
        for spec in creator_contracts
        if spec.get("external_range_class")
        and str((spec or {}).get("public_tool") or "").strip()
    ]
    object_props = sorted(
        standalone_relationship_tool_contracts(
            context.contract.get("relationship_tool_contracts") or {},
            context.contract.get("materialization_operation_units") or {},
        )
    )
    composite_enabled = bool(
        (
            context.contract.get("materialization_operation_units") or {}
        ).get("merged_predicate_locals")
    )
    ontology = _py_name(context.ontology.name)
    has_om2_quantity = bool(context.contract.get("om2_quantity_properties"))
    top_local = str(
        (context.contract.get("top_entity") or {}).get("class_local") or ""
    ).strip()
    class_ancestor_map = {
        cls: _class_ancestors(context.parsed.get("classes") or {}, cls)
        for cls in classes
    }
    ordered_parent_classes_with_subclasses = sorted(
        cls
        for cls in _ordered_member_classes(context)
        if any(
            cls in ancestors
            for other, ancestors in class_ancestor_map.items()
            if other != cls
        )
    )
    top_link_specs: list[dict[str, str]] = []
    required_step_scoped_specs = [
        {
            "domain": str((spec or {}).get("domain_local") or "").strip(),
            "predicate": str((spec or {}).get("predicate_local") or "").strip(),
            "range": str((spec or {}).get("range_local") or "").strip(),
        }
        for spec in context.contract.get("required_step_scoped_object_properties") or []
        if str((spec or {}).get("domain_local") or "").strip()
        and str((spec or {}).get("predicate_local") or "").strip()
        and str((spec or {}).get("range_local") or "").strip()
    ]
    mutually_exclusive_groups = _mutually_exclusive_property_groups(context)
    if top_local:
        top_cls = (context.parsed.get("classes") or {}).get(top_local) or {}
        for prop, range_local in sorted(
            (top_cls.get("object_properties") or {}).items()
        ):
            if str(prop).strip() and str(range_local).strip():
                target_stem = _normalized_symbol(_predicate_target_stem(str(prop)))
                accepted_classes = sorted(
                    cls
                    for cls in classes
                    if cls == str(range_local)
                    or str(range_local) in (class_ancestor_map.get(cls) or [])
                    or _normalized_symbol(cls) == target_stem
                )
                top_link_specs.append(
                    {
                        "predicate": str(prop),
                        "range": str(range_local),
                        "accepted_classes": accepted_classes,
                        "required": False,
                        "ordered_member": False,
                    }
                )
    for spec in context.contract.get("required_links") or []:
        pred = _local_name((spec or {}).get("predicate_iri"))
        target = _local_name((spec or {}).get("target_class_iri"))
        if pred and target:
            existing = next(
                (
                    item
                    for item in top_link_specs
                    if item.get("predicate") == pred and item.get("range") == target
                ),
                None,
            )
            if existing is not None:
                existing["required"] = True
                existing["ordered_member"] = bool((spec or {}).get("ordered_member"))
            else:
                target_stem = _normalized_symbol(_predicate_target_stem(pred))
                top_link_specs.append(
                    {
                        "predicate": pred,
                        "range": target,
                        "accepted_classes": sorted(
                            cls
                            for cls in classes
                            if cls == target
                            or target in (class_ancestor_map.get(cls) or [])
                            or _normalized_symbol(cls) == target_stem
                        ),
                        "required": True,
                        "ordered_member": bool((spec or {}).get("ordered_member")),
                    }
                )
    imports = [
        "import inspect",
        "import json",
        "import os",
        "from pathlib import Path",
        "from fastmcp import FastMCP",
        *(
            [
                "from . import _fixed_rdf_runtime as rdf_runtime",
                "from ._fixed_rdf_runtime import init_memory",
            ]
            if composite_enabled
            else ["from ._fixed_rdf_runtime import init_memory, export_memory"]
        ),
        f"from .{ontology}_creation_entities import "
        + ", ".join(
            [
                *(
                    f"create_{_py_name(cls)} as _create_{_py_name(cls)}"
                    for cls in classes
                ),
                *(f"{tool} as _{tool}" for tool in external_creator_tools),
                *(
                    ["create_om2_quantity as _create_om2_quantity"]
                    if has_om2_quantity
                    else []
                ),
            ]
        ),
        f"from .{ontology}_creation_checks import "
        + ", ".join(
            [
                "check_ordered_members as _check_ordered_members",
                *(f"{tool} as _{tool}" for tool in check_tools),
            ]
        ),
    ]
    if object_props:
        imports.append(
            f"from .{ontology}_creation_relationships import "
            + ", ".join(
                f"add_{_py_name(prop)} as _add_{_py_name(prop)}"
                for prop in object_props
            )
        )
    parts = [
        "from __future__ import annotations\n\n",
        "\n".join(imports),
        """

def _normalize_fastmcp_optional_signature(function):
    signature = inspect.signature(function)
    parameters = [
        parameter.replace(kind=inspect.Parameter.POSITIONAL_OR_KEYWORD)
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is not inspect.Parameter.empty
        else parameter
        for parameter in signature.parameters.values()
    ]
    function.__signature__ = signature.replace(parameters=parameters)
    return function

""",
        f"""mcp = FastMCP(name={context.ontology.name!r})


def _load_tool_metadata() -> dict:
    if os.environ.get("TWA_MCP_TOOL_DESCRIPTIONS_ENABLED", "").strip() != "1":
        return {{}}
    path = Path(__file__).with_name("tool_descriptions.json")
    if not path.is_file():
        return {{}}
    return json.loads(path.read_text(encoding="utf-8"))


_TOOL_METADATA = _load_tool_metadata()
_TOOL_DESCRIPTIONS = {{
        str(name): str(description).strip()
        for name, description in (_TOOL_METADATA.get("descriptions") or {{}}).items()
        if str(name).strip() and str(description).strip()
    }}
_GENERATED_MCP_INSTRUCTION = str(
    _TOOL_METADATA.get("instruction") or ""
).strip()
_FAST_MCP_TOOL = mcp.tool


def _tool_with_generated_description(*args, **kwargs):
    name = str(kwargs.get("name") or "").strip()
    description = _TOOL_DESCRIPTIONS.get(name)
    if description and not kwargs.get("description"):
        kwargs["description"] = description
    return _FAST_MCP_TOOL(*args, **kwargs)


mcp.tool = _tool_with_generated_description

""",
    ]
    create_map = (
        "{\n"
        + "".join(f"    {cls!r}: _create_{_py_name(cls)},\n" for cls in classes)
        + "".join(f"    {tool!r}: _{tool},\n" for tool in external_creator_tools)
        + "}"
    )
    add_map = (
        "{\n"
        + "".join(f"    {prop!r}: _add_{_py_name(prop)},\n" for prop in object_props)
        + "}"
    )
    parts.append("""@mcp.prompt(name="instruction")
def instruction() -> str:
    if _GENERATED_MCP_INSTRUCTION:
        return _GENERATED_MCP_INSTRUCTION
    return (
        "Use the available MCP tools to mutate and export the RDF graph. "
        "Never report that RDF triples were created, linked, exported, or validated unless "
        "the corresponding tools were actually called and returned successfully. "
        "A prose-only response is not a successful KG-building run. Use the class-specific "
        "create tools and property-specific add tools explicitly, then call export_memory."
    )

""")
    for tool in external_creator_tools:
        parts.append(
            f"mcp.tool(name={tool!r})(_normalize_fastmcp_optional_signature(_{tool}))\n"
        )
    # Hint-to-tool orchestration belongs to the KG agent and its prompt. Generated
    # packages expose only atomic create/add/lifecycle tools.
    if False:
        parts.append(
            f'''_CREATE_TOOLS = {create_map}
_ADD_TOOLS = {add_map}
_CLASS_ANCESTORS = {class_ancestor_map!r}
_ORDERED_MEMBER_CLASSES = {sorted(_ordered_member_classes(context))!r}
_ORDERED_PARENT_CLASSES_WITH_SUBCLASSES = {ordered_parent_classes_with_subclasses!r}
_ORDERING_PROPERTY_LOCALS = {sorted(_ordering_datatype_properties(context))!r}
_REQUIRED_STEP_SCOPED_OBJECT_PROPERTIES = {required_step_scoped_specs!r}
_MUTUALLY_EXCLUSIVE_PROPERTY_GROUPS = {mutually_exclusive_groups!r}
_TOP_CLASS_LOCAL = {top_local!r}
_TOP_LINK_SPECS = {top_link_specs!r}


def _payload_items(value):
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _parse_hints_payload(hints_json: str):
    text = str(hints_json or "").strip()
    if not text:
        return {{}}
    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char not in "{{[":
            continue
        try:
            payload, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("hints_json must contain a JSON object")


def _canonical_hint_class(class_local: str) -> str:
    candidate = str(class_local or "").strip()
    if candidate.startswith("create_"):
        candidate = candidate[len("create_"):]
    if candidate in _CREATE_TOOLS:
        return candidate
    if candidate.startswith("create_"):
        maybe_class = candidate[len("create_"):]
        if maybe_class in _CREATE_TOOLS:
            return maybe_class
    if "#" in candidate:
        maybe_class = candidate.split("#", 1)[0]
        if maybe_class in _CREATE_TOOLS:
            return maybe_class
    parts = candidate.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit() and parts[0] in _CREATE_TOOLS:
        return parts[0]
    return candidate


def _call_create(class_local: str, payload: dict, fallback_label: str) -> str:
    class_local = _canonical_hint_class(class_local)
    fn = _CREATE_TOOLS.get(class_local)
    if fn is None:
        raise ValueError(f"Unsupported class in hints: {{class_local}}")
    params = set(inspect.signature(fn).parameters)
    label = str(payload.get("label") or fallback_label or class_local).strip() or class_local
    kwargs = {{
        key: value
        for key, value in payload.items()
        if key != "label" and key in params and value is not None and str(value) != ""
    }}
    result = json.loads(fn(label, **kwargs))
    iri = str(result.get("iri") or "").strip()
    if not iri:
        raise ValueError(f"create_{{class_local}} did not return an IRI")
    return iri


def _class_matches_range(class_local: str, range_local: str) -> bool:
    return class_local == range_local or range_local in (_CLASS_ANCESTORS.get(class_local) or [])


def _class_matches_top_link(class_local: str, spec: dict) -> bool:
    accepted_classes = {{str(x) for x in (spec.get("accepted_classes") or [])}}
    range_local = str(spec.get("range") or "")
    return class_local in accepted_classes or _class_matches_range(class_local, range_local)


def _class_matches_domain(class_local: str, domain_local: str) -> bool:
    return class_local == domain_local or domain_local in (_CLASS_ANCESTORS.get(class_local) or [])


def _is_ordered_member_class(class_local: str) -> bool:
    return class_local in _ORDERED_MEMBER_CLASSES or any(
        ancestor in _ORDERED_MEMBER_CLASSES for ancestor in (_CLASS_ANCESTORS.get(class_local) or [])
    )


def _extract_order_value(class_local: str, payload: dict) -> int:
    for prop in _ORDERING_PROPERTY_LOCALS:
        if prop not in payload or payload.get(prop) in (None, ""):
            continue
        value = payload.get(prop)
        if isinstance(value, bool):
            raise ValueError(f"{{class_local}}.{{prop}} must be a positive integer starting at 1")
        text = str(value).strip()
        if isinstance(value, int):
            order = value
        elif re.fullmatch(r"\\d+", text):
            order = int(text)
        elif re.fullmatch(r"\\d+\\.0+", text):
            order = int(float(text))
        else:
            raise ValueError(f"{{class_local}}.{{prop}} must be a positive integer starting at 1")
        if order < 1:
            raise ValueError(f"{{class_local}}.{{prop}} must be a positive integer starting at 1")
        return order
    raise ValueError(f"Ordered-member class {{class_local}} is missing a T-Box ordering property")


def _normalize_match_text(value) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("α", "alpha").replace("β", "beta").replace("γ", "gamma").replace("δ", "delta")
    text = text.replace("₀", "0").replace("₁", "1").replace("₂", "2").replace("₃", "3").replace("₄", "4")
    text = text.replace("₅", "5").replace("₆", "6").replace("₇", "7").replace("₈", "8").replace("₉", "9")
    text = re.sub(r"\\s*[\\(\\[][^\\)\\]]*[0-9][^\\)\\]]*[\\)\\]]\\s*$", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _index_hint_labels_by_class(hints: dict) -> dict[str, list[str]]:
    labels_by_class: dict[str, list[str]] = {{}}
    for raw_class, value in hints.items():
        class_local = _canonical_hint_class(raw_class)
        for payload in _payload_items(value):
            label = str(payload.get("label") or "").strip()
            if label:
                labels_by_class.setdefault(class_local, []).append(label)
    return labels_by_class


def _existing_graph_labels_for_class(class_local: str) -> list[str]:
    labels: list[str] = []
    class_iri = NS[class_local]
    for subject in GRAPH.subjects(RDF.type, class_iri):
        for label in GRAPH.objects(subject, RDFS.label):
            text = str(label or "").strip()
            if text:
                labels.append(text)
    return labels


def _augment_required_step_scoped_labels(class_local: str, payload: dict, labels_by_class: dict[str, list[str]]) -> dict:
    augmented = dict(payload)
    if not _is_ordered_member_class(class_local):
        return augmented
    member_label_norm = _normalize_match_text(augmented.get("label"))
    if not member_label_norm:
        return augmented
    for spec in _REQUIRED_STEP_SCOPED_OBJECT_PROPERTIES:
        domain_local = str(spec.get("domain") or "")
        predicate = str(spec.get("predicate") or "")
        range_local = str(spec.get("range") or "")
        param = f"{{predicate}}_label"
        if not (domain_local and predicate and range_local) or not _class_matches_domain(class_local, domain_local):
            continue
        if augmented.get(param):
            continue
        matches = []
        candidate_labels = [
            *labels_by_class.get(range_local, []),
            *_existing_graph_labels_for_class(range_local),
        ]
        for candidate in candidate_labels:
            candidate_norm = _normalize_match_text(candidate)
            if candidate_norm and candidate_norm in member_label_norm:
                matches.append(candidate)
        unique_matches = list(dict.fromkeys(matches))
        if len(unique_matches) == 1:
            augmented[param] = unique_matches[0]
    return augmented


def _payload_information_score(payload: dict) -> int:
    score = 0
    for key, value in payload.items():
        if key == "label" or value is None or str(value).strip() == "":
            continue
        if isinstance(value, list):
            score += len([item for item in value if item is not None and str(item).strip() != ""])
        else:
            score += 1
    return score


def _is_active_exclusive_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return any(_is_active_exclusive_value(item) for item in value)
    if isinstance(value, bool):
        return value
    text = str(value).strip()
    if not text:
        return False
    return text.lower() not in {{"-", "0", "false", "no", "none", "null", "n/a"}}


def _apply_mutually_exclusive_property_groups(class_local: str, payload: dict) -> dict:
    if not _MUTUALLY_EXCLUSIVE_PROPERTY_GROUPS:
        return payload
    normalized = dict(payload)
    for group in _MUTUALLY_EXCLUSIVE_PROPERTY_GROUPS:
        target = str(group.get("target_class") or "")
        if not target or not _class_matches_domain(class_local, target):
            continue
        properties = [str(prop) for prop in (group.get("properties") or []) if str(prop)]
        active = [
            prop
            for prop in properties
            if prop in normalized and _is_active_exclusive_value(normalized.get(prop))
        ]
        if len(active) > 1:
            raise ValueError(
                f"Mutually exclusive property group for {{class_local}} has multiple active values: "
                + ", ".join(active)
            )
    return normalized


def _ordered_member_signature(class_local: str, payload: dict) -> str:
    label_norm = _normalize_match_text(payload.get("label"))
    return label_norm or _normalize_match_text(class_local)


def _ordering_property_for_payload(payload: dict) -> str:
    for prop in _ORDERING_PROPERTY_LOCALS:
        if prop in payload:
            return prop
    return _ORDERING_PROPERTY_LOCALS[0] if _ORDERING_PROPERTY_LOCALS else "ordering"


def _synthesize_missing_required_ordered_members(raw_items: list[dict], labels_by_class: dict[str, list[str]]) -> list[dict]:
    if not _ORDERING_PROPERTY_LOCALS:
        return raw_items
    next_pos = -1
    synthesized: list[dict] = []
    for spec in _REQUIRED_STEP_SCOPED_OBJECT_PROPERTIES:
        domain_local = str(spec.get("domain") or "")
        predicate = str(spec.get("predicate") or "")
        range_local = str(spec.get("range") or "")
        if not (domain_local and predicate and range_local) or domain_local not in _CREATE_TOOLS:
            continue
        if any(item.get("ordered") and _class_matches_domain(str(item.get("class_local") or ""), domain_local) for item in raw_items):
            continue
        candidate_labels = [
            *labels_by_class.get(range_local, []),
            *_existing_graph_labels_for_class(range_local),
        ]
        unique_labels = [label for label in dict.fromkeys(candidate_labels) if str(label or "").strip()]
        for offset, label in enumerate(unique_labels, start=1):
            payload = {{
                "label": f"{{domain_local}} {{label}}",
                _ORDERING_PROPERTY_LOCALS[0]: offset,
                f"{{predicate}}_label": label,
            }}
            synthesized.append({{
                "class_local": domain_local,
                "payload": payload,
                "fallback_idx": offset,
                "ordered": True,
                "original_pos": next_pos,
                "order": offset,
                "signature": _ordered_member_signature(domain_local, payload),
                "score": _payload_information_score(payload),
            }})
            next_pos -= 1
    if not synthesized:
        return raw_items
    return synthesized + raw_items


def _prepare_materialization_items(hints: dict, labels_by_class: dict[str, list[str]]) -> list[dict]:
    raw_items: list[dict] = []
    for raw_class, value in hints.items():
        canonical_class = _canonical_hint_class(raw_class)
        if canonical_class == _TOP_CLASS_LOCAL:
            continue
        if canonical_class in _ORDERED_PARENT_CLASSES_WITH_SUBCLASSES:
            continue
        for idx, payload in enumerate(_payload_items(value), start=1):
            payload = _apply_mutually_exclusive_property_groups(canonical_class, payload)
            payload = _augment_required_step_scoped_labels(canonical_class, payload, labels_by_class)
            item = {{
                "class_local": canonical_class,
                "payload": payload,
                "fallback_idx": idx,
                "ordered": _is_ordered_member_class(canonical_class),
                "original_pos": len(raw_items),
            }}
            if item["ordered"]:
                item["order"] = _extract_order_value(canonical_class, payload)
                item["signature"] = _ordered_member_signature(canonical_class, payload)
                item["score"] = _payload_information_score(payload)
            raw_items.append(item)
    raw_items = _synthesize_missing_required_ordered_members(raw_items, labels_by_class)

    by_same_operation: dict[tuple[int, str], dict] = {{}}
    skipped_positions: set[int] = set()
    for item in raw_items:
        if not item.get("ordered"):
            continue
        key = (int(item["order"]), str(item.get("signature") or ""))
        existing = by_same_operation.get(key)
        if existing is None:
            by_same_operation[key] = item
            continue
        if int(item.get("score") or 0) > int(existing.get("score") or 0):
            skipped_positions.add(int(existing["original_pos"]))
            by_same_operation[key] = item
        else:
            skipped_positions.add(int(item["original_pos"]))

    kept_items = [item for item in raw_items if int(item["original_pos"]) not in skipped_positions]
    ordered_items = [item for item in kept_items if item.get("ordered")]
    orders = [int(item["order"]) for item in ordered_items]
    if len(set(orders)) != len(orders):
        for new_order, item in enumerate(
            sorted(ordered_items, key=lambda x: (int(x["order"]), int(x["original_pos"]))),
            start=1,
        ):
            item["payload"][_ordering_property_for_payload(item["payload"])] = new_order
            item["order"] = new_order
    return kept_items


def _reachable_from_top(top_iri: str) -> set[str]:
    if not top_iri:
        return set()
    reachable: set[str] = set()
    frontier = [URIRef(top_iri)]
    while frontier:
        current = frontier.pop()
        current_text = str(current)
        if current_text in reachable:
            continue
        reachable.add(current_text)
        for _, predicate, obj in GRAPH.triples((current, None, None)):
            if predicate in {{RDF.type, RDFS.label}}:
                continue
            if isinstance(obj, URIRef) and str(obj) not in reachable:
                frontier.append(obj)
    return reachable


def _created_nodes_unreachable(top_iri: str, created: dict[str, list[str]]) -> list[str]:
    reachable = _reachable_from_top(top_iri)
    if not reachable:
        return []
    created_nodes = [
        iri
        for class_local, iris in created.items()
        if class_local != _TOP_CLASS_LOCAL
        for iri in iris
    ]
    return sorted(iri for iri in created_nodes if iri not in reachable)


'''
        )
    if composite_enabled:
        parts.append("""def export_memory(doi: str, top_level_entity_name: str) -> str:
    validation_json = _check_ordered_members()
    validation = json.loads(validation_json)
    if validation.get("status") != "ok":
        return validation_json
    return rdf_runtime.export_memory(doi, top_level_entity_name)


""")
    parts.append("""mcp.tool(name="init_memory")(init_memory)
mcp.tool(name="export_memory")(export_memory)

""")
    for cls in classes:
        fn = _py_name(cls)
        parts.append(f"""mcp.tool(name="create_{fn}")(_normalize_fastmcp_optional_signature(_create_{fn}))

""")
    if has_om2_quantity:
        parts.append("""mcp.tool(name="create_om2_quantity")(_normalize_fastmcp_optional_signature(_create_om2_quantity))

""")
    for prop in object_props:
        fn = _py_name(prop)
        parts.append(f"""mcp.tool(name="add_{fn}")(_add_{fn})

""")
    parts.append("""mcp.tool(name="check_ordered_members")(_check_ordered_members)

""")
    for tool in check_tools:
        parts.append(
            f"""mcp.tool(name="{tool}")(_{tool})

"""
        )
    parts.append("""if __name__ == "__main__":
    mcp.run(transport="stdio")
""")
    return "".join(parts)


def generate_deterministic_script_slice(context: AgenticGenerationContext) -> list[str]:
    scripts_dir = Path(context.scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    ontology = _py_name(context.ontology.name)
    files = {
        "__init__.py": "",
        f"{ontology}_creation_base.py": _base_script(context),
        f"{ontology}_creation_checks.py": _checks_script(context),
        f"{ontology}_creation_entities.py": _entities_script(context),
        f"{ontology}_creation_relationships.py": _relationships_script(context),
        "main.py": _main_script(context),
    }
    occurrence = (context.contract.get("occurrence_surface_units") or {})
    if occurrence.get("public_tools") or occurrence.get("public_linkers"):
        from src.agents.scripts_and_prompts_generation.occurrence_surface_scripts import (
            emit_occurrence_argument_ownership,
            emit_occurrence_loop_guard,
            emit_occurrence_main,
            emit_occurrence_operations,
        )
        from src.agents.scripts_and_prompts_generation.occurrence_surface_units import (
            ARGUMENT_OWNERSHIP_FILENAME,
            LOOP_GUARD_FILENAME,
        )

        files[f"{ontology}_occurrence_operations.py"] = emit_occurrence_operations(
            context, occurrence
        )
        files["main.py"] = emit_occurrence_main(context, occurrence)
        files[LOOP_GUARD_FILENAME] = emit_occurrence_loop_guard(occurrence)
        files[ARGUMENT_OWNERSHIP_FILENAME] = emit_occurrence_argument_ownership(
            occurrence
        )
    written: list[str] = []
    for name, content in files.items():
        path = scripts_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    return written


def write_agentic_mcp_main_py(context: AgenticGenerationContext) -> str:
    """Regenerate only MCP ``main.py`` (expects sibling deterministic *_creation_*.py modules)."""
    scripts_dir = Path(context.scripts_dir)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = scripts_dir / "main.py"
    path.write_text(_main_script(context), encoding="utf-8")
    return str(path)


def _format_property_rows(context: AgenticGenerationContext, *, kind: str) -> str:
    rows: list[str] = []
    for name, prop in sorted((context.parsed.get("properties") or {}).items()):
        if (prop or {}).get("kind") != kind:
            continue
        if not _prompt_includes_property(context, name, prop or {}, kind=kind):
            continue
        domains = ", ".join(str(x) for x in ((prop or {}).get("domains") or []))
        rng = str((prop or {}).get("range") or "")
        comment = str((prop or {}).get("comment") or "").strip()
        value_kind = str((prop or {}).get("value_kind") or "").strip()
        row = f"- `{name}`: domains=[{domains}], range=`{rng}`"
        if value_kind:
            row += f", value_kind=`{value_kind}`"
        if comment:
            row += f"; comment={comment}"
        rows.append(row)
    return "\n".join(rows) if rows else "- None declared in the T-Box."


def _format_value_kind_priority_contract(context: AgenticGenerationContext) -> str:
    """Emit a T-Box-derived value-kind inventory and domain-agnostic priority rules."""
    properties = context.parsed.get("properties") or {}
    by_kind: dict[str, list[str]] = {}
    for name, prop in sorted(properties.items()):
        if (prop or {}).get("kind") != "datatype":
            continue
        if not _prompt_includes_property(context, name, prop or {}, kind="datatype"):
            continue
        value_kind = str((prop or {}).get("value_kind") or "").strip()
        if not value_kind:
            continue
        by_kind.setdefault(value_kind, []).append(name)

    if not by_kind:
        return ""

    lines = [
        "Value-Kind Priority Contract:",
        "- When the T-Box marks a datatype field with `value_kind`, treat that annotation as authoritative for extraction priority and value shape.",
        "- `binary_checklist` fields outrank `free_text_fallback` and ordinary free-text fields for the same source fact: if a binary/canonical field fits, emit that field and do not park the same fact only in a catch-all/free-text fallback.",
        "- For every `binary_checklist` field listed below, evaluate source evidence before returning JSON; emit the T-Box-configured active checklist value when supported, otherwise omit the field.",
        "- For every `free_text_fallback` field listed below, emit text only for explicit source items that no binary/canonical sibling field covers; do not use fallbacks to restate facts already captured by binary fields.",
        "- For `derived` fields, do not invent values from prose alone when the T-Box says they are computed from other extracted fields.",
        "- Do not skip emitting a class section merely because only binary checklist fields are supported for that class; if any binary field is source-supported, emit the class section with those fields.",
    ]
    preferred_order = [
        "binary_checklist",
        "free_text_fallback",
        "free_text",
        "derived",
    ]
    for kind in preferred_order:
        names = by_kind.get(kind) or []
        if not names:
            continue
        joined = ", ".join(f"`{name}`" for name in names)
        lines.append(f"- value_kind=`{kind}` fields: {joined}")
    for kind, names in sorted(by_kind.items()):
        if kind in preferred_order:
            continue
        joined = ", ".join(f"`{name}`" for name in names)
        lines.append(f"- value_kind=`{kind}` fields: {joined}")
    return "\n".join(lines)


def _prompt_field_allowlist(context: AgenticGenerationContext) -> dict[str, set[str]]:
    """T-Box fields are exhaustive; runtime config cannot hide semantic fields."""
    return {}


def _prompt_class_is_filtered(
    context: AgenticGenerationContext, class_local: str
) -> bool:
    return class_local in _prompt_field_allowlist(context)


def _prompt_field_allowed(
    context: AgenticGenerationContext, class_local: str, field: str
) -> bool:
    allowlist = _prompt_field_allowlist(context)
    if class_local not in allowlist:
        return True
    return field == "label" or field in allowlist[class_local]


def _filter_prompt_datatype_props(
    context: AgenticGenerationContext, class_local: str, props: set[str]
) -> set[str]:
    return {
        prop
        for prop in props
        if _prompt_field_allowed(context, class_local, _py_name(prop))
    }


def _filter_prompt_object_props(
    context: AgenticGenerationContext, class_local: str, props: set[str]
) -> set[str]:
    return {
        prop
        for prop in props
        if _prompt_field_allowed(context, class_local, f"{_py_name(prop)}_ref")
    }


def _prompt_includes_property(
    context: AgenticGenerationContext, name: str, prop: dict[str, Any], *, kind: str
) -> bool:
    allowlist = _prompt_field_allowlist(context)
    if not allowlist:
        return True
    domains = [str(x) for x in (prop.get("domains") or []) if str(x)]
    if kind == "datatype":
        field = _py_name(name)
        return any(_prompt_field_allowed(context, domain, field) for domain in domains)
    if kind == "object":
        field = f"{_py_name(name)}_ref"
        return any(_prompt_field_allowed(context, domain, field) for domain in domains)
    return True


def _format_class_rows(context: AgenticGenerationContext) -> str:
    rows: list[str] = []
    ordered_classes = _ordered_member_classes(context)
    ordering_props = _ordering_datatype_properties(context)
    for name, cls in sorted((context.parsed.get("classes") or {}).items()):
        data_prop_set = set(((cls or {}).get("datatype_properties") or {}).keys())
        if name in ordered_classes:
            data_prop_set.update(ordering_props)
        data_prop_set = _filter_prompt_datatype_props(context, name, data_prop_set)
        data_props = ", ".join(sorted(data_prop_set)) or "none"
        object_prop_set = set(((cls or {}).get("object_properties") or {}).keys())
        object_prop_set.update(_step_scoped_object_properties_for_class(context, name))
        object_prop_set = _filter_prompt_object_props(context, name, object_prop_set)
        if (
            _prompt_class_is_filtered(context, name)
            and not data_prop_set
            and not object_prop_set
        ):
            continue
        obj_props = ", ".join(sorted(object_prop_set)) or "none"
        parents = (
            ", ".join(_class_ancestors(context.parsed.get("classes") or {}, name))
            or "none"
        )
        comment = str((cls or {}).get("comment") or "").strip()
        row = f"- `{name}`: parents=[{parents}], datatype_properties=[{data_props}], object_properties=[{obj_props}]"
        if comment:
            row += f"; comment={comment}"
        rows.append(row)
    return "\n".join(rows)


def _tbox_comment_fidelity_contract() -> str:
    return """T-Box Comment Fidelity Contract:
- Treat every `comment=` value in the Classes and Properties, Datatype Properties, and Object Properties sections as a binding extraction rule and normative extraction constraint, not as background prose.
- When a class/property comment narrows evidence, defines normalization, gives positive/negative examples, or states conditional gates, apply those rules before emitting any field.
- A comment containing `【Warning】` marks a high-risk choice boundary. Before selecting or excluding any class or field governed by such a comment, compare the source against that complete comment and every applicable warning-marked alternative, including positive thresholds, exclusions, priority, and non-duplication rules.
- The warning marker changes attention only. Never infer a domain-specific trigger, example, priority, or exception from this generic instruction; all choice semantics must come from the marked T-Box comments.
- Treat negation, prevention, avoidance, risk-only, planned-but-not-performed, and rule-out contexts as negative evidence unless the T-Box comment explicitly says they count as positive evidence.
- If source evidence conflicts with or falls short of a T-Box comment requirement, omit the field rather than filling a plausible value."""


def _generated_create_tool_fields(
    context: AgenticGenerationContext, class_local: str
) -> list[str]:
    """Return fields owned by the class-specific atomic create tool."""
    creator = next(
        (
            item
            for item in _owned_entity_tool_contracts(context)
            if str(item.get("class_local") or "") == class_local
        ),
        None,
    )
    if creator is None:
        return _generated_owner_scalar_fields(context, class_local)
    fields = ["label"]
    fields.extend(
        str(item.get("property_local") or "")
        for item in creator.get("datatype_inputs") or []
        if str(item.get("property_local") or "")
    )
    for edge in creator.get("required_edges") or []:
        if edge.get("target_resolution") == "existing_iri_parameter":
            fields.append(str(edge.get("parameter_name") or ""))
        elif edge.get("target_resolution") == "same_operation_create":
            fields.append(str(edge.get("label_parameter") or ""))
            fields.extend(
                str(item.get("parameter_name") or "")
                for item in edge.get("datatype_inputs") or []
                if str(item.get("parameter_name") or "")
            )
    return list(dict.fromkeys(field for field in fields if field))


def _generated_owner_scalar_fields(
    context: AgenticGenerationContext, class_local: str
) -> list[str]:
    """Return only source-ledger fields, excluding graph-wiring parameters."""
    classes = context.parsed.get("classes") or {}
    cls = classes.get(class_local) or {}
    data_props = set((cls.get("datatype_properties") or {}).keys())
    if class_local in _ordered_member_classes(context):
        data_props.update(_ordering_datatype_properties(context))
    data_props = _filter_prompt_datatype_props(context, class_local, data_props)
    return ["label", *sorted(_py_name(prop) for prop in data_props)]


def _generated_hint_fields(
    context: AgenticGenerationContext, class_local: str
) -> list[str]:
    """Return extraction fields with stable refs and ref-only relationships."""
    classes = context.parsed.get("classes") or {}
    cls = classes.get(class_local) or {}
    fields = ["ref", *_generated_owner_scalar_fields(context, class_local)]
    required_by_domain: dict[str, list[dict[str, Any]]] = {}
    for spec in context.contract.get("required_step_scoped_object_properties") or []:
        domain = str((spec or {}).get("domain_local") or "").strip()
        if domain:
            required_by_domain.setdefault(domain, []).append(spec)
    required_prop_names = {
        str((spec or {}).get("predicate_local") or "").strip()
        for spec in required_by_domain.get(class_local, [])
    }

    object_props = {
        str(prop): str(range_local or "").strip()
        for prop, range_local in ((cls.get("object_properties") or {}).items())
        if str(prop).strip() and str(range_local or "").strip()
    }
    object_props.update(_step_scoped_object_properties_for_class(context, class_local))
    object_props = {
        prop: range_local
        for prop, range_local in object_props.items()
        if _prompt_field_allowed(context, class_local, f"{_py_name(prop)}_ref")
    }
    for prop in sorted(object_props):
        if prop not in required_prop_names:
            fields.append(f"{_py_name(prop)}_ref")
    for spec in required_by_domain.get(class_local, []):
        prop = str((spec or {}).get("predicate_local") or "target").strip()
        if prop:
            fields.append(f"{_py_name(prop)}_ref")
    return list(dict.fromkeys(fields))


def _format_subclass_comment_contract(
    context: AgenticGenerationContext,
    *,
    allowed_classes: set[str] | None = None,
) -> str:
    """Render subclass comments directly from the parsed T-Box."""
    rows: list[str] = []
    classes = context.parsed.get("classes") or {}
    for class_local, cls in sorted(classes.items()):
        if allowed_classes is not None and class_local not in allowed_classes:
            continue
        parent_classes = sorted(
            {
                str(parent).strip()
                for parent in (cls or {}).get("parent_classes") or []
                if str(parent).strip() in classes
            }
        )
        comment = str((cls or {}).get("comment") or "").strip()
        if parent_classes and comment:
            rows.append(
                f"- Subclass comment for `{class_local}` "
                f"(parents: {', '.join(f'`{parent}`' for parent in parent_classes)}): "
                + comment
            )
    return "\n".join(rows)


def _format_materializable_hint_contract(
    context: AgenticGenerationContext,
    *,
    allowed_classes: set[str] | None = None,
    allowed_object_properties: set[str] | None = None,
    lexical_object_properties: set[str] | None = None,
) -> str:
    rows: list[str] = []
    emitted_relations: set[tuple[str, str, str]] = set()
    lexical_properties = set(lexical_object_properties or set())
    merged_properties = {
        str(value)
        for value in (
            (
                context.contract.get("materialization_operation_units") or {}
            ).get("merged_predicate_locals")
            or []
        )
        if str(value)
    }
    for class_local in sorted((context.parsed.get("classes") or {}).keys()):
        if allowed_classes is not None and class_local not in allowed_classes:
            continue
        classes = context.parsed.get("classes") or {}
        cls = classes.get(class_local) or {}
        object_props = {
            str(prop): str(range_local or "").strip()
            for prop, range_local in ((cls.get("object_properties") or {}).items())
            if str(prop).strip() and str(range_local or "").strip()
        }
        object_props.update(
            _step_scoped_object_properties_for_class(context, class_local)
        )
        datatype_fields = [
            field
            for field in _generated_owner_scalar_fields(context, class_local)
            if field != "label"
        ]
        datatype_fields.extend(
            prop for prop in sorted(object_props) if prop in lexical_properties
        )
        datatype_fields = list(dict.fromkeys(datatype_fields))
        if _prompt_class_is_filtered(context, class_local) and not datatype_fields:
            continue
        fields = (
            ", ".join(f"`{field}`" for field in datatype_fields)
            or "none"
        )
        rows.append(
            f"- Entity class `{class_local}` -> `datatype_properties` accepts: {fields}"
        )
        property_contracts = context.parsed.get("properties") or {}
        for field in datatype_fields:
            comment = str(
                (property_contracts.get(field) or {}).get("comment") or ""
            ).strip()
            if comment:
                rows.append(
                    f"  - Field `{field}` semantic contract: {comment}"
                )
        for prop, range_local in sorted(object_props.items()):
            if prop in lexical_properties or prop in merged_properties:
                continue
            if (
                allowed_object_properties is not None
                and prop not in allowed_object_properties
            ):
                continue
            emitted_relations.add((prop, class_local, range_local))
            rows.append(
                f"- Relation `{prop}`: `subject_ref` class `{class_local}` -> "
                f"`object_ref` class `{range_local}`"
            )
    if allowed_object_properties is not None:
        top_entity_local = str(
            (context.contract.get("top_entity") or {}).get("class_local") or ""
        ).strip()
        allowed_subject_classes = (
            set(allowed_classes or set()) | ({top_entity_local} if top_entity_local else set())
            if allowed_classes is not None
            else None
        )
        for prop in sorted(allowed_object_properties):
            if prop in lexical_properties or prop in merged_properties:
                continue
            spec = (context.parsed.get("properties") or {}).get(prop) or {}
            if str(spec.get("kind") or "") != "object":
                continue
            range_local = str(spec.get("range") or "").strip()
            domains = [
                str(value).strip()
                for value in (spec.get("domains") or [spec.get("domain")])
                if str(value or "").strip()
            ]
            for domain_local in domains:
                if (
                    allowed_subject_classes is not None
                    and domain_local not in allowed_subject_classes
                ):
                    continue
                relation = (prop, domain_local, range_local)
                if relation in emitted_relations:
                    continue
                emitted_relations.add(relation)
                rows.append(
                    f"- Relation `{prop}`: `subject_ref` class `{domain_local}` -> "
                    f"`object_ref` class `{range_local}`"
                )
    integrity_contract = _format_subclass_comment_contract(
        context,
        allowed_classes=allowed_classes,
    )
    if integrity_contract:
        rows.extend(integrity_contract.splitlines())
    return "\n".join(rows)


def _format_create_tool_contract(
    context: AgenticGenerationContext,
    *,
    allowed_classes: set[str] | None = None,
) -> str:
    rows: list[str] = []
    for class_local in sorted((context.parsed.get("classes") or {}).keys()):
        if allowed_classes is not None and class_local not in allowed_classes:
            continue
        fields = _generated_create_tool_fields(context, class_local)
        if _prompt_class_is_filtered(context, class_local) and fields == ["label"]:
            continue
        rows.append(
            f"- `create_{_py_name(class_local)}` accepts only: "
            + ", ".join(f"`{field}`" for field in fields)
        )
    return "\n".join(rows)


def _format_atomic_operation_contract(
    context: AgenticGenerationContext,
    *,
    allowed_classes: set[str] | None = None,
) -> str:
    rows: list[str] = []
    for creator in _owned_entity_tool_contracts(context):
        class_local = str(creator.get("class_local") or "")
        if allowed_classes is not None and class_local not in allowed_classes:
            continue
        tool_name = str(creator.get("public_tool") or "")
        for edge in creator.get("required_edges") or []:
            predicate = str(edge.get("predicate_local") or "")
            if edge.get("target_resolution") == "existing_iri_parameter":
                parameter = str(edge.get("parameter_name") or "")
                rows.append(
                    f"- `{tool_name}` owns `{predicate}`: pass the existing scoped parent "
                    f"IRI as `{parameter}` in the creator call. The creator writes this edge; "
                    f"never call `add_{predicate}`."
                )
            elif edge.get("target_resolution") == "same_operation_create":
                label_parameter = str(edge.get("label_parameter") or "")
                datatype_parameters = [
                    str(item.get("parameter_name") or "")
                    for item in edge.get("datatype_inputs") or []
                    if str(item.get("parameter_name") or "")
                ]
                parameter_text = ", ".join(
                    f"`{value}`"
                    for value in [label_parameter, *datatype_parameters]
                    if value
                )
                rows.append(
                    f"- `{tool_name}` owns `{predicate}` and its fresh "
                    f"`{edge.get('dependent_class_local')}` target: pass the target data through "
                    f"{parameter_text or 'the declared owned-dependent parameters'} in the same "
                    f"creator call. Do not call a second creator or `add_{predicate}` for it."
                )
    return (
        "\n".join(rows)
        if rows
        else "- No creator-owned object-property edges are declared for this iteration."
    )


def _format_linked_target_scalar_contract(context: AgenticGenerationContext) -> str:
    classes = context.parsed.get("classes") or {}
    rows: list[str] = []
    for source_class, cls in sorted(classes.items()):
        object_props = {
            str(prop): str(range_local or "").strip()
            for prop, range_local in (
                (cls or {}).get("object_properties") or {}
            ).items()
            if str(prop).strip() and str(range_local or "").strip()
        }
        object_props.update(
            _step_scoped_object_properties_for_class(context, source_class)
        )
        for prop, target_class in sorted(object_props.items()):
            target_fields = [
                field
                for field in _generated_create_tool_fields(context, target_class)
                if field != "label" and not field.endswith("_ref")
            ]
            if target_fields:
                rows.append(
                    f"- `{source_class}.{_py_name(prop)}_ref` links to `{target_class}`; "
                    f"if source text states scalar attributes for that linked target, also emit a `{target_class}` object "
                    f"with the exact referenced `ref`, a canonical `label`, and supported scalar fields: {', '.join(f'`{field}`' for field in target_fields)}."
                )
    if not rows:
        return "- No object-reference targets with scalar fields are declared in the generated tool contract."
    return "\n".join(rows)


def _format_required_step_scoped_object_contract(
    context: AgenticGenerationContext,
) -> str:
    rows: list[str] = []
    merged_properties = {
        str(value)
        for value in (
            (
                context.contract.get("materialization_operation_units") or {}
            ).get("merged_predicate_locals")
            or []
        )
        if str(value)
    }
    for spec in context.contract.get("required_step_scoped_object_properties") or []:
        domain = str((spec or {}).get("domain_local") or "").strip()
        prop = str((spec or {}).get("predicate_local") or "").strip()
        target = str((spec or {}).get("range_local") or "").strip()
        if domain and prop and target and prop not in merged_properties:
            rows.append(
                f"- `{domain}` requires `{_py_name(prop)}_ref` linking to `{target}`. "
                f"When an extracted `{domain}` names a source-supported `{target}` already present in the hints, "
                f"emit the exact stable `{target}` ref in `{_py_name(prop)}_ref` so identity and scalar fields remain separate."
            )
    if not rows:
        return (
            "- No required ordered-member object-reference links are declared in the T-Box."
        )
    return "\n".join(rows)


def _format_required_links(context: AgenticGenerationContext) -> str:
    rows: list[str] = []
    for spec in context.contract.get("required_links") or []:
        pred = (
            str((spec or {}).get("predicate_iri") or "").rstrip("/#").rsplit("/", 1)[-1]
        )
        target = (
            str((spec or {}).get("target_class_iri") or "")
            .rstrip("/#")
            .rsplit("/", 1)[-1]
        )
        min_count = (spec or {}).get("min_count", 1)
        rows.append(f"- `{pred}` -> `{target}` with min_count={min_count}")
    return (
        "\n".join(rows)
        if rows
        else "- No required top-level links declared in the runtime contract."
    )


def _format_ordered_member_contract(context: AgenticGenerationContext) -> str:
    profile = context.contract.get("ordered_member_profile") or {}
    ordered_classes = sorted(_ordered_member_classes(context))
    ordering_props = sorted(_ordering_datatype_properties(context))
    member_props = sorted(
        str(x)
        for x in profile.get("individually_linked_object_properties", []) or []
        if str(x).strip()
    )
    subclass_targets = profile.get("most_specific_subclass_targets") or {}
    lines: list[str] = []
    if ordered_classes:
        lines.append(
            f"- Ordered member classes from T-Box: {', '.join(f'`{x}`' for x in ordered_classes)}."
        )
    if ordering_props:
        lines.append(
            f"- Ordering scalar properties from T-Box: {', '.join(f'`{x}`' for x in ordering_props)}."
        )
        lines.append(
            "- Ordering scalar values must be unique positive integers starting at 1, with no duplicates, gaps, zero, negative, decimal, fractional, or between-step values; renumber ordered members as consecutive integers in source order when inserting or refining steps."
        )
    if member_props:
        lines.append(
            f"- Parent-to-member link properties from T-Box: {', '.join(f'`{x}`' for x in member_props)}."
        )
    for parent, children in sorted(subclass_targets.items()):
        child_list = ", ".join(f"`{x}`" for x in sorted(children or []))
        if child_list:
            lines.append(
                f"- When evidence supports a specific subclass of `{parent}`, create that subclass and preserve the `{parent}` type assertion."
            )
    if not lines:
        return "- No ordered-member integrity contract declared in the T-Box."
    atomic_members: dict[str, tuple[str, str, str]] = {}
    for creator in _owned_entity_tool_contracts(context):
        class_local = str(creator.get("class_local") or "")
        for edge in creator.get("required_edges") or []:
            if edge.get("role") != "container_membership":
                continue
            atomic_members[class_local] = (
                str(creator.get("public_tool") or ""),
                str(edge.get("predicate_local") or ""),
                str(edge.get("parameter_name") or ""),
            )
    if atomic_members:
        for class_local, (tool_name, predicate, parameter) in sorted(
            atomic_members.items()
        ):
            lines.append(
                f"- For each hinted `{class_local}`, call `{tool_name}` with its positive-integer "
                f"ordering value and the scoped parent IRI in `{parameter}`. This atomic creator "
                f"writes `{predicate}`; never follow it with `add_{predicate}`."
            )
        standalone_classes = sorted(set(ordered_classes) - set(atomic_members))
        if standalone_classes:
            merged_predicates = {
                str(value)
                for value in (
                    (
                        context.contract.get("materialization_operation_units") or {}
                    ).get("merged_predicate_locals")
                    or []
                )
                if str(value)
            }
            if merged_predicates.intersection(member_props):
                lines.append(
                    "- Ordered classes without a creator-owned membership edge "
                    f"({', '.join(f'`{value}`' for value in standalone_classes)}) are not "
                    "materializable through this closed atomic surface; do not create them or "
                    "invent a removed parent-to-member tool."
                )
            else:
                lines.append(
                    "- For ordered classes without a creator-owned membership edge "
                    f"({', '.join(f'`{value}`' for value in standalone_classes)}), use the exposed "
                    "parent-to-member relationship tool after creation."
                )
    else:
        lines.append(
            "- For every hinted ordered member, call the specific `create_*` tool with its positive-integer ordering scalar value, then link it individually to the scoped top entity using the configured parent-to-member link."
        )
    lines.append(
        "- Label ordered members deterministically as the current entity label, class local name, and order value when the hint does not provide a better source label."
    )
    lines.append(
        "- Do not satisfy an ordered-member required link by reusing a shell or placeholder member from another top entity."
    )
    return "\n".join(lines)


def _top_entity_selection_contract(context: AgenticGenerationContext) -> str:
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "Entity").strip() or "Entity"
    top_class = (context.parsed.get("classes") or {}).get(top_local) or {}
    top_comment = str((top_class or {}).get("comment") or "").strip()
    rules = [
        f"- Candidate labels must satisfy the T-Box class comment for `{top_local}`.",
        "- Apply the class-level inclusion/exclusion rules before accepting headings, captions, tables, or SI procedure titles.",
        "- If a candidate is disallowed by the T-Box comment or runtime policy, omit it even when the source contains an explicit procedure-like heading.",
        "- If the source is ambiguous under the T-Box rules, omit the candidate rather than expanding recall.",
        f"- Never output the runtime context label `top`, the class local name `{top_local}`, or generated shell labels such as `{top_local}-1` inside brackets; the bracket value must be the source-supported entity identifier, e.g. `{top_local}-1 [UMC-1]`.",
    ]
    if top_comment:
        rules.append(f"- T-Box class comment for `{top_local}`: {top_comment}")
    return "\n".join(rules)


def _extraction_prompt(context: AgenticGenerationContext) -> str:
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "Entity").strip() or "Entity"
    return f"""# Extraction Prompt: {context.ontology.name}

Task:
Extract only information explicitly supported by the source document for the selected ontology.

Ontology Source:
- T-Box TTL: `{context.ontology.ttl_file}`
- Top entity class: `{top.get("class_local") or ""}`

Source Document:
{{paper_content}}

Core Rules:
- Do not infer missing values.
- Preserve source wording for labels and scalar values.
- Use only classes, properties, and constraints derived from the T-Box.
- If evidence is ambiguous, omit the field rather than guessing.

Top-Entity Selection Contract:
{_top_entity_selection_contract(context)}

{_tbox_comment_fidelity_contract()}

Classes and Properties:
{_format_class_rows(context)}

Datatype Properties:
{_format_property_rows(context, kind="datatype")}

Object Properties:
{_format_property_rows(context, kind="object")}

Required Top-Level Links:
{_format_required_links(context)}

Output:
Return only normalized top-entity lines, with no JSON, markdown fences, bullets, or explanatory text.
Each line must use this exact shape:
{top_local}-1 [source-supported label]

If multiple top entities are explicitly present, increment the number:
{top_local}-2 [source-supported label]

The label inside brackets should be the shortest stable source-supported identifier for the selected top entity.
"""


def _format_reuse_tool_contract(
    context: AgenticGenerationContext,
    *,
    allowed_classes: set[str] | None = None,
) -> str:
    checks = existing_entity_check_contracts(
        parsed=context.parsed,
        contract=context.contract,
    )
    if allowed_classes is not None:
        checks = [
            item
            for item in checks
            if str(item.get("class_local") or "").strip() in allowed_classes
        ]
    if not checks:
        return (
            "- No class exposes an existing-entity lookup in this generated tool surface."
        )
    lines = [
        "- Existing-entity lookup and generic reuse are separate permissions.",
        "- Checks with `lookup_scope=central` inspect only ontology-wide candidates; checks with `lookup_scope=document` inspect only the current DOI's document memory. Pass the complete proposed entity hint as `proposed_entity_json`; an independent LLM identity judge evaluates every visible candidate.",
        "- A central or document check returns only candidates authorized by that judge. Each returned candidate includes a scope-bound `reuse_authorization_token`; pass that exact token to every `add_*` call that uses the returned IRI.",
        "- Class-level eligibility never authorizes a particular candidate by itself. If the judge rejects, fails, or returns no candidate, create a new entity.",
        "- Checks with `lookup_scope=scoped` are for non-reusable occurrence classes. They inspect only the current retained scoped graph and may resolve an exact occurrence created in an earlier iteration.",
        "- A scoped check never authorizes deduplication, cross-occurrence reuse, cross-top-entity reuse, or cross-document reuse. Its returned IRI may only be referenced when all occurrence identity details match the current hint.",
        "- Every check returns structured JSON containing `iri`, `labels`, `types`, "
        "`datatype_values`, `outgoing_relations`, `incoming_relations`, lookup metadata, and central provenance when applicable.",
    ]
    for item in checks:
        if item.get("reuse_authorized"):
            lines.append(
                f"- `{item['class_local']}`: call `{item['public_tool']}` first; "
                f"pass the proposed hint JSON; lookup_scope=`{item['lookup_scope']}`; candidate reuse "
                f"requires an LLM authorization token under policy scope=`{item['reuse_scope']}`; "
                f"match basis: {item['match_basis']}"
            )
        else:
            lines.append(
                f"- `{item['class_local']}`: call `{item['public_tool']}` for exact "
                f"occurrence reference resolution; lookup_scope=`scoped`; generic reuse "
                f"forbidden; occurrence basis: {item['match_basis']}"
            )
    return "\n".join(lines)


def _format_reusable_label_contract(context: AgenticGenerationContext) -> str:
    checks = existing_entity_check_contracts(
        parsed=context.parsed,
        contract=context.contract,
    )
    class_locals = sorted(
        {
            str(item.get("class_local") or "").strip()
            for item in checks
            if item.get("reuse_authorized") is True
            if str(item.get("class_local") or "").strip()
        }
    )
    if not class_locals:
        return "- No class is authorized for generic reuse."
    return "\n".join(
        [
            "- Reusable classes: " + ", ".join(f"`{name}`" for name in class_locals) + ".",
            "- For every entity of a reusable class, use the shortest stable source-supported identity label. The label must be independent of the Current Target Entity.",
            "- Never add the Current Target Entity label as a prefix, suffix, parenthetical qualifier, or phrase such as `for <Current Target Entity>` merely to encode processing scope.",
            "- Represent document/top-entity scope only through object-property links, runtime provenance, and the entity IRI; never encode scope by changing a reusable entity's canonical label.",
            "- This label rule does not authorize reuse by label. Reuse requires a positive independent LLM pair judgement and its runtime authorization token.",
        ]
    )


def _kg_prompt(context: AgenticGenerationContext) -> str:
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "").strip()
    return f"""# KG Building Prompt: {context.ontology.name} Iteration 1

Task:
Export the pipeline-seeded top-entity A-Box scope without creating a root.

Runtime Inputs:
- Document identifier: `{{doi}}`
- Source document: `{{paper_content}}`
- Extracted top entities/hints: `{{top_entities}}`

Identity Contract:
- The Iteration-1 identity lock/dossier is the sole authority for `{top_local}` identity.
- First call `init_memory(doi, top_level_entity_name)` using the orchestrator-supplied scope.
- Bind each source-supported bracketed label to the exact locked URI restored by `init_memory`.
- Never call a top-root creator, mint a replacement URI, deduplicate locked scopes, or retype a locked root.
- If a hinted label is absent from the lock/dossier, report an upstream identity blocker.
- Attach only source-supported facts explicitly allowed by the active T-Box and this pass when such an exposed tool is available.
- Finally call `export_memory(doi, top_level_entity_name)` with the same scope. Do not call another tool afterward.
- Do not claim success unless export returns non-empty scoped A-Box Turtle.

Ontology Source:
- T-Box TTL: `{context.ontology.ttl_file}`
- Locked top entity class: `{top_local}`
"""
    required_tool_lines: list[str] = []
    if context.ontology.role == "main" and top_local:
        required_tool_lines.append(
            f"- Top-entity KG pass only: bind each `{top_local}` label to the exact URI "
            "already seeded in the pipeline identity lock; do not call a top-root creator."
        )
    else:
        for spec in context.contract.get("required_links") or []:
            pred = (
                str((spec or {}).get("predicate_iri") or "")
                .rstrip("/#")
                .rsplit("/", 1)[-1]
            )
            target = (
                str((spec or {}).get("target_class_iri") or "")
                .rstrip("/#")
                .rsplit("/", 1)[-1]
            )
            if bool((spec or {}).get("ordered_member")):
                continue
            if pred and target:
                required_tool_lines.append(
                    f"- Only when the extraction hints contain a concrete source-supported `{target}` object, call `create_{_py_name(target)}` for its label, then call `add_{_py_name(pred)}` from the `{top_local}` IRI to that target IRI."
                )
    required_tool_text = (
        "\n".join(required_tool_lines)
        if required_tool_lines
        else "- No required link tools are declared."
    )
    integrity = json.dumps(context.integrity_profile, indent=2, ensure_ascii=False)
    top_tool_rules = (
        f"- Bind every source-supported `{top_local}` label to its exact URI in the "
        "pipeline-seeded identity lock/dossier. Never call a top-root creator, mint a "
        "replacement, or retype the locked root."
        if top_local
        else "- The T-Box declares no machine-readable top role; do not guess or create one."
    )
    reuse_tool_contract = _format_reuse_tool_contract(context)
    return f"""# KG Building Prompt: {context.ontology.name}

Task:
Create RDF triples using the generated MCP tools for this ontology only.

Runtime Inputs:
- Document identifier: `{{doi}}`
- Source document: `{{paper_content}}`
- Extracted top entities/hints: `{{top_entities}}`

Ontology Source:
- T-Box TTL: `{context.ontology.ttl_file}`
- Top entity class: `{top.get("class_local") or ""}`

Tool-Use Rules:
- You must call tools. A prose-only answer is a failed run.
- First call `init_memory(doi, top_level_entity_name)` with the current document identifier and configured top-level entity context.
{top_tool_rules}
- Reuse the scoped top entity when the contract says top-entity reuse is required.
- Maintain a runtime map from every hint `ref` to exactly one materialized IRI.
- For reusable classes, call the class's central `check_existing_*` tool with the complete proposed entity hint serialized as JSON. Reuse only a returned LLM-authorized candidate, and preserve its `reuse_authorization_token` for relationship calls.
- Whenever an `add_*` relation uses a central candidate IRI, pass that candidate's `reuse_authorization_token`; an absent, mismatched, or stale token is a hard rejection.
- For non-reusable classes, call the class's scoped `check_existing_*` tool only when a ref denotes an exact occurrence already created in this scoped run. A result may resolve that prior ref for linking; it never authorizes deduplicating a newly declared occurrence.
- A matching label alone never proves either reusable identity or occurrence identity.
- In the top-entity KG pass, do not create placeholder/shell targets for required links. In particular, do not create generic ordered-member targets just to satisfy a required link; later per-entity iterations must create source-supported members with concrete subclasses and ordering values.
- Do not materialize generic ordered-member parent hints as placeholder members when specific ordered-member subclasses exist in the T-Box.
- Do not create two ordered-member individuals for the same source operation, label, or ordering value; choose the single most specific class supported by the T-Box class comments.
- Do not export duplicate same-class labels for the same extracted entity, and do not leave unreachable typed nodes outside the graph reachable from the scoped top entity.
- Create one RDF individual per extracted entity unless the ontology-derived integrity profile says reuse is allowed.
- Add object-property links only when both subject and object are supported by extracted evidence.
- Hint `ref`, `class`, and relation fields are orchestration metadata; never pass them to a `create_*` tool.
- A `create_*` tool creates exactly one individual and accepts only that class's label and datatype fields. It never creates or links an object-property target.
- Materialize entity records before relation records. Pass each entity's `label` plus `datatype_properties` to its class creator and bind the returned IRI to its `ref`.
- For each relation, resolve `subject_ref` and `object_ref` through that map, then call the matching `add_*` tool.
- Never derive datatype values from a label or ref.
- If a referenced target has no source-supported companion entity and no eligible existing instance, do not invent it merely to satisfy a relationship.
- Never alter a canonical label to encode the scoped top entity or any numerical/context payload.
- Enforce every configured mutually exclusive property group before materializing hints; never assert more than one active property from the same group on one entity instance.
- Before export, ensure only source-supported required top-level links below are present; absence of a concrete target in the extraction hints is not permission to invent one.
- Do not introduce classes or properties that are not present in the T-Box context.
- Finish by calling `export_memory(doi, top_level_entity_name)` with the same scope; do not claim success until this tool returns successfully.

Required Tool Sequence:
{required_tool_text}

Existing Entity Lookup Contract:
{reuse_tool_contract}

Required Top-Level Links:
{_format_required_links(context)}

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

Atomic Create Tool Contract:
{_format_create_tool_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
Ontology-Derived Integrity Profile:
```json
{integrity}
```
{_configured_prompt_addon(context)}
Export:
After entity creation and linking, call the export tool and ensure the emitted Turtle parses successfully.
"""


def _iteration_extraction_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    responsibilities = iteration.get("responsibilities") or {}
    owned_classes = ", ".join(
        str(value)
        for value in responsibilities.get("classes") or []
        if str(value).strip()
    ) or "none"
    owned_properties = ", ".join(
        str(value)
        for value in responsibilities.get("object_properties") or []
        if str(value).strip()
    ) or "none"
    external_tools = {
        str(name).strip()
        for name in (
            iteration.get("extraction_mcp_tools") or iteration.get("mcp_tools") or []
        )
        if str(name).strip()
    }
    enrichment_profile = (
        iteration.get("external_enrichment_profile")
        if isinstance(iteration.get("external_enrichment_profile"), dict)
        else {}
    )
    allowed_classes = set((context.parsed.get("classes") or {}).keys())
    allowed_properties = set((context.parsed.get("properties") or {}).keys())
    target_class = str(enrichment_profile.get("target_class") or "").strip()
    field_map = {
        str(source): str(target)
        for source, target in (enrichment_profile.get("field_map") or {}).items()
        if str(source).strip()
        and str(target).strip()
        and str(target) in allowed_properties
    }
    enrichment_provider = str(enrichment_profile.get("provider") or "").strip()
    enrichment_lines: list[str] = []
    if (
        enrichment_provider
        and enrichment_provider in external_tools
        and target_class in allowed_classes
        and field_map
    ):
        enrichment_lines = [
            "External enrichment contract:",
            f"- Use `{enrichment_provider}` only for source-supported `{target_class}` identities.",
            "- Preserve the source label and map provider values only to these T-Box fields:",
            *(
                f"  - `{source}` -> `{target}`"
                for source, target in sorted(field_map.items())
            ),
            "- External results may enrich an existing source-supported identity, but never prove participation or create a new source fact.",
        ]
    external_enrichment = "\n".join(enrichment_lines)
    reusable_label_contract = _format_reusable_label_contract(context)
    if str(iteration.get("hint_representation") or "").strip() == "semantic-text.v1":
        from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
            _semantic_text_natural_ledger_rules,
        )

        ledger_rules = "\n".join(
            f"- {rule}" for rule in _semantic_text_natural_ledger_rules()
        )
        return f"""# Extraction Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Extract source-supported semantic-text.v1 hints for this iteration only.

Iteration Scope:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}
- Stage-owned classes: {owned_classes}
- Stage-owned object properties: {owned_properties}

Current Target Entity:
- Label: {{entity_label}}
- IRI: {{entity_uri}}

Rules:
- Return only a natural-language ledger headed exactly `SEMANTIC_HINTS_V1`.
- Do not emit JSON, RDF, refs, IRIs, endpoint IDs, quantity nodes, tool calls, or graph layout.
{ledger_rules}
- Preserve exact source quantity lexemes in the owning occurrence, either in the prose or as `<predicate_local>: <lexeme>`.
- Leave entity identity resolution and graph construction to the KG-building agent.
- Treat accumulated prior hints as read-only context, not a completeness mask for this iteration.

{external_enrichment}
T-Box-Derived Subclass Integrity Contract:
{_format_subclass_comment_contract(context)}

Reusable Entity Label Contract:
{reusable_label_contract}

{_tbox_comment_fidelity_contract()}

Source:
{{paper_content}}
"""
    return f"""# Extraction Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Extract source-supported hints for this iteration only.

Iteration Scope:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}
- Stage-owned classes: {owned_classes}
- Stage-owned object properties: {owned_properties}

Current Target Entity:
- Label: {{entity_label}}
- IRI: {{entity_uri}}

Rules:
- Use only the source text and the ontology-derived class/property context below.
- Treat every T-Box `comment=` value below as a binding extraction rule for the corresponding class or property.
- Extract hints for the current target entity only.
- Treat the pipeline-injected accumulated prior hints as a read-only semantic identity registry.
- Every semantic entity must have one stable `ref`. Reuse the exact prior `ref`, class, and canonical label when a new stage-owned relation references an entity already present in accumulated prior hints.
- Do not re-emit a prior semantic entity under a new `ref` or altered label, and do not repeat a prior relation.
- Emit entity records only for stage-owned classes, except for the minimal Current Target Entity identity record required to attach new stage-owned relations.
- If a source fact is already represented in accumulated prior hints, omit it from this iteration rather than restating it.
- Prior existence never satisfies a new stage-owned property by itself. For every stage-owned property, compare the source with the accumulated hints and emit each source-supported property that is not already represented.
- When a new stage-owned property links two prior entities, emit only the relation record and reuse both exact prior refs. A ref-only relation is not a duplicate entity.
- Do not emit classes described as reserved, future, optional placeholders, or execution anchors unless the source explicitly contains an instance required by this iteration.
- Prefer source spans whose headings, labels, or local context match the current target entity label.
- Do not copy fields from another top entity when the current target entity has distinct source evidence.
- Before emitting JSON, find the class section whose `label` equals the Current Target Entity label; for that section, scan the Materializable Hint Contract and include every source-supported datatype field accepted by that class. Identifier values in lists or tables paired with the target label are source-supported evidence.
- Emit only fields that are both source-supported and listed in the Materializable Hint Contract below.
- Omit missing, uncertain, or unsupported fields.
- Treat structured source labels, table headers, bullet labels, and nearby section labels as strong evidence for matching datatype fields when their normalized words match the field local name, CSV-style display label, or T-Box comment wording.
- For explicit structured source sections, tables, or bullet lists, evaluate every listed item before returning JSON; do not stop after the first matching field in the section.
- If a T-Box comment contains an explicit convention, positive example, negative example, priority instruction, exception, or "do not" rule, apply that instruction literally and before using general keyword matching.
- If a property comment says a field is not supported by a source phrase, requires a more specific source condition, or prefers a sibling/canonical field for that phrase, follow that comment even when the phrase contains words that otherwise resemble the field name.
- If a property comment defines normalization examples or says which modifiers to keep or remove, emit the normalized value required by the comment rather than preserving the full source phrase.
- If a class exposes a catch-all, other, note, or free-text datatype field and its T-Box comment says to use it for explicit items not covered by canonical fields, collect all source-listed unmatched items for that class in that field.
- If the T-Box marks datatype fields with `value_kind=binary_checklist`, evaluate every such field for the relevant class against the source before returning JSON; binary/canonical checklist fields have higher priority than free-text or catch-all fallback fields for the same fact.
- If the T-Box marks datatype fields with `value_kind=free_text_fallback`, use them only for explicit source items that no binary/canonical sibling field covers; never use a fallback to replace or hide a source-supported binary field.
- When a linked class has only binary checklist fields supported by the source, still emit its entity record and the corresponding relation record; do not drop it because no free-text field was filled.
- For identification or demographic fields, inspect source header blocks and nearby header tables before the main body when the class or property comments say those fields come from identifying or administrative source regions.
- For short acronym-like datatype fields, if the exact acronym/token appears in source text in a relevant diagnosis, indication, observation, or field-value context and the T-Box comment says that token activates the field, emit the configured active checklist value for that field.
- If source evidence supports any datatype field for a class linked from the current top entity, emit both its entity record and the relation record from the top entity. After emitting one datatype field, scan the same structured source region for all other accepted fields of that class.
- Use exact class local names in each entity record's `class` field and exact property local names in each relation record's `property` field.
- Each entity record must separate `ref`, `class`, canonical `label`, and `datatype_properties`. Datatype values belong only inside `datatype_properties`.
- Emit a class section only when the current target label or a source-supported linked target actually denotes an instance of that class; do not coerce one semantic category into another merely because both classes are available.
- For ordered-member classes, ordering fields listed in the Materializable Hint Contract must be unique positive integers starting at 1. Do not emit duplicate, skipped, decimal, fractional, zero, negative, or between-step order values such as 1.5; renumber the ordered members as consecutive integers in source order.
- Do not emit the same source operation, label, or ordering value as two different ordered-member class sections; choose the single most specific class supported by the T-Box class comments.
- Do not emit a generic ordered-member parent class as a placeholder when source evidence supports one of its specific subclasses in the Materializable Hint Contract; emit the specific subclass section and its supported fields instead.
- If the T-Box declares an ordered-member class whose comment says it introduces a linked target object, emit one ordered member per source-supported linked target in source order.
- Labels are identity names only. Never append amounts, concentrations, durations, temperatures, roles, or other scalar/context payload to `label`; put those values in `datatype_properties`.
- Relations must use `subject_ref`, `property`, and `object_ref`; never use a label as an object reference. If the linked target is already present in prior hints, reuse its exact ref.
- For every entity class, never invent a scoped label such as `<source label> for <Current Target Entity>` or `<source label> (<Current Target Entity>)` unless that complete phrase is explicitly the entity's own name in the source. Contextual participation must be represented by links, not label text.
- A `ref` identifies exactly one semantic entity occurrence and must not be shared by different class instances. Repeated occurrences of a non-reusable class require different refs even when their canonical labels match.
- For ordered-member relations whose target has scalar fields, point to the same target ref whose entity record carries those scalar fields; never mint a second ref merely to transport values.
- For every required ordered-member object link declared below, emit a relation record when the source identifies a target object present in current or prior hints.
- For multiple linked targets, emit one relation record per target ref and one entity record per newly introduced target.
- If a linked target label also explicitly supplies a value accepted by a target scalar field, preserve that source-supported value in the scalar field.
- Follow object-property comments when they require source-supported links to target objects; do not omit a required link merely because another scalar field was filled.
- If a general parameter string contains a scalar value that has a dedicated generated field in the Materializable Hint Contract, also emit the dedicated field instead of leaving the scalar only inside the parameter string.
- For classes listed in the Mutually Exclusive Property Contract, emit at most one active property from each group for one entity instance. Use source evidence and T-Box comments to choose the best-supported one; omit the rest.
- When a T-Box comment distinguishes final, confirmed, provisional, preliminary, intermediate, or subordinate evidence, follow that evidence priority exactly; do not promote provisional or intermediate source statements into final/confirmed fields unless the source explicitly makes them final or confirmed.
- If the T-Box exposes a procedure-inheritance object field and the source says the current procedure follows the same, similar, or previous conditions as another source-supported procedure, treat the referenced procedure text as source-supported context for this target: carry over its ordered members and linked targets, then apply only the explicit modifications stated for the current target.
- Existing refs are not source evidence for adding new relations; emit a relation only when the Source text explicitly supports it.
- If evidence belongs to a more specific class whose generated tool lists the relevant field, emit that specific class instead of a generic parent/container class.
- Never emit schema placeholders or routing fields; use only the class sections and field names listed below.
- Output only a compact JSON object, with no markdown fences or explanatory prose.

{external_enrichment}
Reusable Entity Label Contract:
{reusable_label_contract}

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

{_format_value_kind_priority_contract(context)}

Linked Target Scalar Contract:
{_format_linked_target_scalar_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_tbox_comment_fidelity_contract()}

Expected JSON Shape:
Hint Schema: ref-entity-relations.v1
- Return exactly one JSON object with top-level arrays `entities` and `relations`.
- Every entity is `{{"ref": "...", "class": "<exact class local>", "label": "<canonical identity label>", "datatype_properties": {{...}}}}`.
- Every relation is `{{"subject_ref": "...", "property": "<exact object-property local>", "object_ref": "..."}}`.
- Every ref used by a relation must resolve to exactly one entity in current or accumulated prior hints.
- Never encode a datatype value in `ref`, `label`, `subject_ref`, or `object_ref`.

Classes and Properties:
{_format_class_rows(context)}

Datatype Properties:
{_format_property_rows(context, kind="datatype")}

Object Properties:
{_format_property_rows(context, kind="object")}

{_configured_prompt_addon(context)}
Source:
{{paper_content}}
"""


def _iteration_kg_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "TopEntity").strip() or "TopEntity"
    semantic_scope = iteration.get("semantic_scope") or {}
    owned_classes = [
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("classes") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    ] or [
        str(value).strip()
        for value in (iteration.get("responsibilities") or {}).get("classes") or []
        if str(value).strip()
    ]
    owned_object_properties = [
        str(item.get("local") or "").strip()
        for item in semantic_scope.get("object_properties") or []
        if isinstance(item, dict) and str(item.get("local") or "").strip()
    ] or [
        str(value).strip()
        for value in (iteration.get("responsibilities") or {}).get(
            "object_properties"
        )
        or []
        if str(value).strip()
    ]
    owned_class_set = set(owned_classes)
    owned_property_set = set(owned_object_properties)
    reuse_tool_contract = _format_reuse_tool_contract(
        context,
        allowed_classes=owned_class_set,
    )
    linked_classes = {
        str(value).strip()
        for value in iteration.get("linked_materialization_classes") or []
        if str(value).strip()
    }
    target_contract_lines: list[str] = []
    relationship_contracts = context.contract.get("relationship_tool_contracts") or {}
    merged_predicates = {
        str(value)
        for value in (
            (
                context.contract.get("materialization_operation_units") or {}
            ).get("merged_predicate_locals")
            or []
        )
    }
    for property_local in owned_object_properties:
        if property_local in merged_predicates:
            continue
        contract = relationship_contracts.get(property_local) or {}
        handling = str(contract.get("target_handling") or "untyped_existing_iri")
        creators = [
            str(value).strip()
            for value in contract.get("creator_tools") or []
            if str(value).strip()
        ]
        ranges = [
            str(value).strip()
            for value in (
                contract.get("materialization_target_locals")
                or contract.get("range_locals")
                or []
            )
            if str(value).strip()
        ]
        if handling == "fixed_runtime_creator":
            instruction = (
                "create the source-supported target with `create_om2_quantity`, then pass "
                "its returned IRI to the relationship tool"
            )
        elif creators:
            instruction = (
                "create a source-supported target with "
                + " or ".join(f"`{tool}`" for tool in creators)
                + ", then pass its returned IRI to the relationship tool"
            )
        else:
            instruction = (
                "use only an existing absolute IRI satisfying the declared range; no target "
                "creator is available"
            )
        target_contract_lines.append(
            f"- `{property_local}` -> [{', '.join(ranges)}] ({handling}): {instruction}."
        )
    target_contract_text = (
        "\n".join(target_contract_lines)
        if target_contract_lines
        else "- No iteration-owned relationship target contract is declared."
    )
    creator_owned_properties = [
        value for value in owned_object_properties if value in merged_predicates
    ]
    owned_classes_text = ", ".join(owned_classes)
    owned_properties_text = ", ".join(owned_object_properties)
    creator_owned_properties_text = ", ".join(creator_owned_properties)
    linked_text = ", ".join(sorted(linked_classes))
    if str(iteration.get("hint_representation") or "").strip() == "semantic-text.v1":
        return f"""# KG Building Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Use the generated MCP tools to materialize semantic-text.v1 iteration hints for this iteration.

Rules:
- Iteration-owned classes: [{owned_classes_text}]
- Iteration-owned object_properties: [{owned_properties_text}]
- Creator-owned atomic object_properties: [{creator_owned_properties_text}]
- Linked materialization classes: [{linked_text}]
- Consume the Iteration Hints block below as a SEMANTIC_HINTS_V1 natural-language semantic ledger. Do not require JSON entities/relations, refs, or datatype_properties objects.
- Derive grounded individuals and relations from the ledger, T-Box comments, and the closed tool surface.
- You must call tools. A prose-only answer is a failed run.
- Reuse the scoped top entity URI supplied by the runtime.
- Treat every additional parameter in the Atomic Create Tool Contract as part of one indivisible business operation. Pass the scoped parent IRI and any source-grounded owned-dependent fields in that creator call; never repeat a creator-owned edge through a separate relationship tool.
- For lexical quantity predicates, recover the complete exact lexeme from the owning occurrence's standalone `P: <lexeme>` line and materialize it via the fixed quantity creator before asserting P.
- Finish by calling `export_memory`; do not claim success until export succeeds.

Mandatory Tool Sequence:
1. Call `init_memory` with the current document identifier and scoped top entity label.
2. Bind the scoped top entity IRI restored by `init_memory`; do not call a top-entity creator in this iteration.
3. Materialize owned entities and links justified by the semantic ledger and relationship target contract.
4. Call `export_memory` and base the final response only on the returned tool result.

Relationship Target Handling Contract:
{target_contract_text}

Scoped Top Entity:
- Document identifier value: `{{doi}}`
- Class: `{top_local}`
- Entity label value: `{{entity_label}}`
- Entity URI value: `{{entity_uri}}`

Required Top-Level Links:
{_format_required_links(context)}

Existing Entity Lookup Contract:
{reuse_tool_contract}

Materializable Hint Contract:
{_format_materializable_hint_contract(context, allowed_classes=owned_class_set, allowed_object_properties=owned_property_set)}

Atomic Create Tool Contract:
{_format_create_tool_contract(context, allowed_classes=owned_class_set | linked_classes)}

Creator-Owned Atomic Edge Contract:
{_format_atomic_operation_contract(context, allowed_classes=owned_class_set)}

{_format_value_kind_priority_contract(context)}

Ordered-Member Integrity Contract:
{_format_ordered_member_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_tbox_comment_fidelity_contract()}

{_configured_prompt_addon(context)}
Iteration Hints:
{{iteration_hints}}
"""
    return f"""# KG Building Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Use the generated MCP tools to materialize the extracted hints for this iteration.

Rules:
- Iteration-owned classes: [{owned_classes_text}]
- Iteration-owned object_properties: [{owned_properties_text}]
- Creator-owned atomic object_properties: [{creator_owned_properties_text}]
- Linked materialization classes: [{linked_text}]
- The iteration-owned lists above are closed and authoritative for primary facts.
  A relationship target may additionally be created only through the exact creator
  named in the Relationship Target Handling Contract below, even when that target
  class is absent from Iteration-owned classes. A referenced prior entity outside
  these paths may be resolved and linked, but must not be recreated.
- You must call tools. A prose-only answer is a failed run.
- Reuse the scoped top entity URI supplied by the runtime.
- Maintain a runtime map from each hint `ref` to exactly one materialized IRI.
- Bind an exact prior IRI from the pipeline identity dossier directly when its explicit fact matches the hinted/ref relation. `init_memory` restores this one-hop prior neighborhood into scoped memory; do not require central lookup or a reuse token for that already-scoped prior IRI.
- For a reusable class newly proposed by this iteration without an exact dossier IRI, call its central `check_existing_*` with the complete proposed entity hint serialized as JSON. Reuse only an LLM-authorized returned IRI and retain its `reuse_authorization_token`.
- Whenever an `add_*` relation uses a central candidate IRI, pass that candidate's `reuse_authorization_token`; without a valid scope-bound token the runtime rejects the link.
- For a non-reusable class, its `check_existing_*` reads only current scoped memory and is reference-resolution-only. Use it to recover the exact IRI for a prior occurrence ref; never use it to merge a newly declared occurrence, even when labels match.
- If an `object_ref` names a prior non-reusable occurrence but no scoped candidate satisfies its class, canonical label, datatype values, and existing relations, report an upstream identity/materialization blocker. Do not create a replacement occurrence merely to satisfy the relation.
- Use exact class/property local names from the extracted hints and T-Box.
- Treat the Materializable Hint Contract as the authoritative extraction schema. Treat the Atomic Create Tool Contract as the authoritative `create_*` parameter schema.
- For datatype values, pass supported scalar fields directly into the relevant `create_*` tool parameters.
- If a `create_*` tool exposes scalar parameters inherited from a parent class, pass the hinted values there; do not omit them because the concrete class is more specific than the parent.
- For ordered-member tools, pass only unique positive integer ordering values starting at 1; if enrichment adds a member between existing members, renumber the full ordered sequence with consecutive integers instead of using duplicate values, gaps, or decimals such as 1.5.
- Do not create two ordered-member individuals for the same source operation, label, or ordering value; choose the single most specific class supported by the T-Box class comments.
- Do not export duplicate same-class labels for the same extracted entity, and do not leave unreachable typed nodes outside the graph reachable from the scoped top entity.
- Do not materialize generic ordered-member parent hints as placeholder members when specific ordered-member subclass hints are available for the same source segment.
- Hint refs, classes, and relation records are orchestration metadata, not `create_*` parameters.
- Each `create_*` tool creates its primary individual and exactly the additional targets or links declared by the Atomic Create Tool Contract. Treat those declared effects as complete when the creator succeeds; never repeat them through separate creator or relationship calls.
- Materialize every standalone entity record first using only its canonical `label` and `datatype_properties`; creator-owned dependent records must instead be passed through the owning atomic creator. Bind each successful returned IRI to its stable `ref`.
- For relation records not listed as creator-owned atomic effects, resolve both endpoints by `subject_ref` and `object_ref`, then call the matching exposed `add_*` tool. Never call or invent an `add_*` tool for a creator-owned effect.
- Never derive datatype values from a label or ref, and never append the scoped top entity or scalar payload to a canonical label.
- Enforce every configured mutually exclusive property group before materializing hints; never assert more than one active property from the same group on one entity instance.
- For object links, call the relevant `add_*` tools after creating/reusing both endpoints.
- Finish by calling `export_memory`; do not claim success until export succeeds.

Mandatory Tool Sequence:
1. Call `init_memory` with the current document identifier and scoped top entity label.
2. Bind the scoped top entity IRI restored by `init_memory`; do not call a top-entity creator in this iteration.
3. Bind exact dossier-provided prior IRIs first. Then materialize each owned entity record once. Newly proposed reusable entities may select only a central candidate returned with a positive LLM judgement and authorization token; each newly declared non-reusable occurrence must call `create_*` and receive a fresh IRI.
4. For every standalone relation record, resolve both refs. Use scoped `check_existing_*` only to resolve an exact prior non-reusable occurrence, then call the matching exposed `add_*` tool. Skip creator-owned effects already completed by a successful atomic creator.
5. For every required top-level link that remains standalone, call the matching exposed `add_*` tool from the scoped top entity to each created target. Creator-owned top-level links stay inside their atomic creator call.
6. For every ordered member, follow the Ordered-Member Integrity Contract below; creator-owned membership edges must stay inside their atomic creator call.
7. Call `export_memory` and base the final response only on the returned tool result.

Failure Condition:
- If you have not called `export_memory`, or if no export result is available, state that KG building failed instead of saying the RDF graph was created or exported.

Relationship Target Handling Contract:
{target_contract_text}

Scoped Top Entity:
- Document identifier value: `{{doi}}`
- Class: `{top_local}`
- Entity label value: `{{entity_label}}`
- Entity URI value: `{{entity_uri}}`

Required Top-Level Links:
{_format_required_links(context)}

Existing Entity Lookup Contract:
{reuse_tool_contract}

Materializable Hint Contract:
{_format_materializable_hint_contract(context, allowed_classes=owned_class_set, allowed_object_properties=owned_property_set)}

Atomic Create Tool Contract:
{_format_create_tool_contract(context, allowed_classes=owned_class_set)}

Creator-Owned Atomic Edge Contract:
{_format_atomic_operation_contract(context, allowed_classes=owned_class_set)}

{_format_value_kind_priority_contract(context)}

Ordered-Member Integrity Contract:
{_format_ordered_member_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_configured_prompt_addon(context)}
Authoritative Extracted Iteration Hints:
- Materialize only the facts in this injected channel for the current entity and iteration.
- Do not read, request, or re-extract raw paper content. Source text is not a KG Iteration 2+ input.
{{iteration_hints}}
"""


def _pre_extraction_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    tbox_scope = _prompt_tbox_slice(context, iteration)
    subclass_contract = _subclass_decision_contract(tbox_scope)
    return f"""# Pre-Extraction Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Build a closed ledger of the shortest source spans relevant to this iteration scope and classify
each operation only under the supplied T-Box-derived scope.

Iteration Scope:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}

Rules:
- Treat every class/property comment and integrity annotation in T-Box-Derived Scope as binding.
- For each in-scope operation atom, apply every relevant Subclass Decision Checklist decision
  point before choosing one most-specific candidate class.
- A T-Box comment containing `【Warning】` marks a high-risk choice boundary. Before selecting
  or excluding a governed candidate, compare the source against the complete marked comment
  and all applicable warning-marked alternatives. The marker changes attention only and adds
  no domain semantics beyond those comments.
- Record one explicit disposition for every operation atom. If no candidate reaches its
  T-Box-derived evidence threshold, mark the atom unresolved instead of omitting it.
- Do not invent domain rules, triggers, examples, or exceptions outside T-Box-Derived Scope.
- Preserve source order and keep distinct source operations distinct.

T-Box-Derived Scope:
{json.dumps(tbox_scope, indent=2, ensure_ascii=False)}

Subclass Decision Checklist:
{json.dumps(subclass_contract, indent=2, ensure_ascii=False)}

Return only the closed evidence ledger, with verbatim source spans and nearby headings.

Source:
{{paper_content}}
"""


def _sub_iteration_extraction_prompt(
    context: AgenticGenerationContext,
    iteration: dict[str, Any],
    sub_iteration: dict[str, Any],
) -> str:
    reusable_label_contract = _format_reusable_label_contract(context)
    return f"""# Enrichment Extraction Prompt: {context.ontology.name} Iteration {sub_iteration.get("iteration_number")}

Task:
Refine or enrich the existing extraction hints for this sub-iteration only.

Parent Iteration:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}

Sub-Iteration Scope:
- Name: {sub_iteration.get("name") or ""}
- Description: {sub_iteration.get("description") or ""}

Current Target Entity:
- Label: {{entity_label}}
- IRI: {{entity_uri}}

Rules:
- Use only the provided source text and existing hints.
- Treat every T-Box `comment=` value below as a binding extraction rule for the corresponding class or property.
- Preserve the `ref`, class, and canonical label of every entity already present in the existing hints. Enrichment must update the matching ref, never create a renamed duplicate.
- Preserve exact class and field names from the Materializable Hint Contract below.
- Omit unsupported additions and any key that is not listed for its class.
- Treat structured source labels, table headers, bullet labels, and nearby section labels as strong evidence for matching datatype fields when their normalized words match the field local name, CSV-style display label, or T-Box comment wording.
- For explicit structured source sections, tables, or bullet lists, evaluate every listed item before returning JSON; do not stop after the first matching field in the section.
- If a T-Box comment contains an explicit convention, positive example, negative example, priority instruction, exception, or "do not" rule, apply that instruction literally and before using general keyword matching.
- If a property comment says a field is not supported by a source phrase, requires a more specific source condition, or prefers a sibling/canonical field for that phrase, follow that comment even when the phrase contains words that otherwise resemble the field name.
- If a property comment defines normalization examples or says which modifiers to keep or remove, emit the normalized value required by the comment rather than preserving the full source phrase.
- If a class exposes a catch-all, other, note, or free-text datatype field and its T-Box comment says to use it for explicit items not covered by canonical fields, collect all source-listed unmatched items for that class in that field.
- For identification or demographic fields, inspect source header blocks and nearby header tables before the main body when the class or property comments say those fields come from identifying or administrative source regions.
- For short acronym-like datatype fields, if the exact acronym/token appears in source text in a relevant diagnosis, indication, observation, or field-value context and the T-Box comment says that token activates the field, emit the configured active checklist value for that field.
- If source evidence supports datatype fields for a linked class, emit or update its entity record and emit the relation by refs. Scan the same source region for all accepted datatype fields before returning JSON.
- Emit a class section only when the current target label, an existing hinted member, or a source-supported linked target actually denotes an instance of that class; do not coerce one semantic category into another merely because both classes are available.
- For ordered-member classes, ordering fields listed in the Materializable Hint Contract must be unique positive integers starting at 1. Do not emit duplicate, skipped, decimal, fractional, zero, negative, or between-step order values such as 1.5; renumber the ordered members as consecutive integers in source order.
- Do not emit the same source operation, label, or ordering value as two different ordered-member class sections; choose the single most specific class supported by the T-Box class comments.
- Do not emit a generic ordered-member parent class as a placeholder when source evidence supports one of its specific subclasses in the Materializable Hint Contract; emit the specific subclass section and its supported fields instead.
- If the T-Box declares an ordered-member class whose comment says it introduces a linked target object, emit one ordered member per source-supported linked target in source order.
- Labels are identity names only. Put all scalar or role payload in `datatype_properties`, never in labels or refs.
- Relations must use `subject_ref`, exact property local name, and `object_ref`. Reuse exact existing refs.
- For every entity class, never invent a scoped label such as `<source label> for <Current Target Entity>` or `<source label> (<Current Target Entity>)` unless that complete phrase is explicitly the entity's own name in the source. Contextual participation must be represented by links, not label text.
- One ref identifies one semantic entity occurrence. Distinct non-reusable occurrences require distinct refs even when their canonical labels match.
- For ordered-member relations, link to the same target ref whose entity record carries the scalar fields.
- Emit one relation record per source-supported target ref; do not collapse multiple targets into one label string.
- If a linked target label also explicitly supplies a value accepted by a target scalar field, preserve that source-supported value in the scalar field.
- Follow object-property comments when they require source-supported links to target objects; do not omit a required link merely because another scalar field was filled.
- If a general parameter string contains a scalar value that has a dedicated generated field in the Materializable Hint Contract, also emit the dedicated field instead of leaving the scalar only inside the parameter string.
- For classes listed in the Mutually Exclusive Property Contract, emit at most one active property from each group for one entity instance. Use source evidence and T-Box comments to choose the best-supported one; omit the rest.
- When a T-Box comment distinguishes final, confirmed, provisional, preliminary, intermediate, or subordinate evidence, follow that evidence priority exactly; do not promote provisional or intermediate source statements into final/confirmed fields unless the source explicitly makes them final or confirmed.
- If the T-Box exposes a procedure-inheritance object field and the source says the current procedure follows the same, similar, or previous conditions as another source-supported procedure, treat the referenced procedure text as source-supported context for this target: carry over its ordered members and linked targets, then apply only the explicit modifications stated for the current target.
- Existing refs are not source evidence for adding new relations; only source-supported relations may be emitted.
- If enrichment evidence belongs to a more specific class whose generated tool lists the relevant field, emit that specific class instead of a generic parent/container class.
- Return only a compact JSON object, with no markdown fences, bullets, checklists, or explanatory prose.
- The JSON must be directly mergeable into the existing `entities` and `relations` arrays.
- To enrich an existing entity, repeat its exact `ref`, `class`, and `label`, then add only supported `datatype_properties`.
- For source-supported links shared by ordered members, emit one relation per affected `subject_ref`.

Reusable Entity Label Contract:
{reusable_label_contract}

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

Linked Target Scalar Contract:
{_format_linked_target_scalar_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_tbox_comment_fidelity_contract()}

Expected JSON Shape:
Hint Schema: ref-entity-relations.v1
- Top-level arrays are `entities` and `relations`.
- Entity records contain exact `ref`, `class`, canonical `label`, and `datatype_properties`.
- Relation records contain exact `subject_ref`, `property`, and `object_ref`.

Classes and Properties:
{_format_class_rows(context)}

Datatype Properties:
{_format_property_rows(context, kind="datatype")}

Object Properties:
{_format_property_rows(context, kind="object")}

{_configured_prompt_addon(context)}
Source and Existing Hints:
{{paper_content}}
"""


def _iteration_kg_onepass_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    """Seed a focused, composable KG fragment for whole-graph one-pass use."""
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "TopEntity").strip() or "TopEntity"
    iteration_number = iteration.get("iteration_number")
    return f"""# KG Building One-Pass Fragment: {context.ontology.name} Iteration {iteration_number}

Task:
Describe the positive materialization responsibilities contributed by Iteration {iteration_number}
to a combined whole-graph KG-building session. This is a composable fragment, not a
standalone iteration run.

Composition rules:
- Preserve this iteration's focused T-Box semantics, occurrence boundaries, creator contracts,
  relationship directions, reuse rules, and materializable fields.
- Treat iteration ownership as the source of this fragment's positive responsibilities, not as
  a session-wide prohibition on operations contributed by other one-pass fragments.
- Do not open, close, export, or independently declare completion of retained memory.
- Do not defer work to another iteration, ignore another hint section, or prohibit creators
  merely because they are owned by another iteration.

Scoped Top Entity:
- Document identifier value: `{{doi}}`
- Class: `{top_local}`
- Entity label value: `{{entity_label}}`
- Entity URI value: `{{entity_uri}}`

Iteration Hint Section:
{{iteration_hints}}
"""


def _iteration_plan(context: AgenticGenerationContext) -> dict[str, Any]:
    if context.ontology.role == "extension":
        ontology = context.ontology.name
        meta_cfg = json.loads(
            Path(context.ontology.meta_task_config_path).read_text(encoding="utf-8")
        )
        extension_cfg = next(
            (
                item
                for item in (
                    (meta_cfg.get("ontologies", {}) or {}).get("extensions", []) or []
                )
                if str((item or {}).get("name") or "").strip() == ontology
            ),
            {},
        )
        mcp_tools = [
            str(tool).strip()
            for tool in (extension_cfg.get("mcp_list") or [])
            if str(tool).strip()
        ]
        mcp_set_name = str(extension_cfg.get("mcp_set_name") or "extension.json")
        prompt_root = Path(context.output_root) / "prompts" / ontology
        domain_runtime = context.config_provenance.get("domain_config") or {}
        iter_num = int(domain_runtime.get("pipeline_iteration_number") or 1)
        # Simple extensions keep a single compiled semantic iteration (profile slot
        # iter2) while runtime may renumber via pipeline_iteration_number.
        compiled_iterations = [
            item
            for item in (context.iteration_blueprint.get("iterations") or [])
            if isinstance(item, dict)
        ]
        if len(compiled_iterations) != 1:
            raise ValueError(
                f"simple_extension {ontology} requires exactly one compiled semantic "
                f"iteration; got {len(compiled_iterations)}"
            )
        semantic_source = compiled_iterations[0]
        responsibilities = dict(semantic_source.get("responsibilities") or {})
        semantic_scope = dict(semantic_source.get("semantic_scope") or {})
        scope_classes = [
            item
            for item in (semantic_scope.get("classes") or [])
            if isinstance(item, dict) and str(item.get("local") or "").strip()
        ]
        if not scope_classes:
            raise ValueError(
                f"simple_extension {ontology} compiled iteration is missing a non-empty "
                "semantic_scope.classes; refusing to materialize hollow runtime iterations"
            )
        output_ttl = (
            "ontomops_output/ontomops_extension_{entity_name}.ttl"
            if ontology == "ontomops"
            else "ontospecies_output/{entity_slugified}.ttl"
        )
        iteration = {
            "iteration_number": iter_num,
            "name": f"{ontology}_extension",
            "description": str(
                semantic_source.get("description")
                or f"T-Box-driven {ontology} extension extraction and KG building."
            ),
            "model_config_key": f"extension_{ontology}",
            "use_agent": False,
            "per_entity": True,
            "responsibilities": responsibilities,
            "semantic_scope": semantic_scope,
            "inputs": {
                "source": "stitched_paper",
                "tbox_path": context.ontology.ttl_file,
            },
            "outputs": {
                "hints_file": f"mcp_run/iter{iter_num}_hints_{{entity_safe}}.txt",
                "prompt_file": f"prompts/iter{iter_num}_extraction/{{entity_safe}}.md",
                "response_file": f"responses/iter{iter_num}_extraction/{{entity_safe}}.md",
                "extraction_file": f"mcp_run_{ontology}/extraction_{{entity_safe}}.txt",
                "extension_prompt_file": f"prompts/{ontology}_kg_building/{{entity_safe}}.md",
                "output_ttl_dir": f"{ontology}_output",
                "output_ttl": output_ttl,
            },
            "mcp_set_name": mcp_set_name,
            "mcp_tools": mcp_tools,
            "agent_model": str(extension_cfg.get("agent_model") or "gpt-4o"),
            "extraction_prompt": str(
                (prompt_root / f"EXTRACTION_ITER_{iter_num}.md")
            ).replace("\\", "/"),
            "kg_building_prompt": str(
                (prompt_root / f"KG_BUILDING_ITER_{iter_num}.md")
            ).replace("\\", "/"),
            "recursion_limit": 500,
        }
        return {"iterations": [iteration]}

    iterations = list(context.iteration_blueprint.get("iterations") or [])

    main_cfg = {
        "mcp_set_name": "run_created_mcp.json",
        "mcp_tools": [f"{context.ontology.name}_mcp"],
    }
    materialized: list[dict[str, Any]] = []
    for raw in iterations:
        if not isinstance(raw, dict):
            continue
        iteration = dict(raw)
        iter_num = iteration.get("iteration_number")
        if not iter_num:
            continue
        iteration["extraction_prompt"] = (
            f"ai_generated_contents/prompts/{context.ontology.name}/EXTRACTION_ITER_{iter_num}.md"
        )
        iteration["kg_building_prompt"] = (
            f"ai_generated_contents/prompts/{context.ontology.name}/KG_BUILDING_ITER_{iter_num}.md"
        )
        iteration["kg_building_onepass_prompt"] = (
            f"ai_generated_contents/prompts/{context.ontology.name}/"
            f"KG_BUILDING_ITER_{iter_num}_ONEPASS.md"
        )
        iteration.setdefault("mcp_set_name", main_cfg["mcp_set_name"])
        iteration.setdefault("mcp_tools", main_cfg["mcp_tools"])
        if iteration.get("has_pre_extraction"):
            iteration["pre_extraction_prompt"] = (
                f"ai_generated_contents/prompts/{context.ontology.name}/PRE_EXTRACTION_ITER_{iter_num}.md"
            )
        for sub_iteration in iteration.get("sub_iterations") or []:
            if not isinstance(sub_iteration, dict):
                continue
            sub_num = str(sub_iteration.get("iteration_number") or "").replace(".", "_")
            if sub_num:
                sub_iteration["extraction_prompt"] = (
                    f"ai_generated_contents/prompts/{context.ontology.name}/EXTRACTION_ITER_{sub_num}.md"
                )
        materialized.append(iteration)
    return {"iterations": materialized}


def generate_runtime_support_slice(
    context: AgenticGenerationContext,
    *,
    iterations: dict[str, Any] | None = None,
) -> list[str]:
    """Materialize run-local pipeline support artifacts from current inputs."""
    written: list[str] = []
    materialized_iterations = (
        iterations if iterations is not None else _iteration_plan(context)
    )
    if materialized_iterations.get("iterations"):
        iterations_dir = (
            Path(context.output_root) / "iterations" / context.ontology.name
        )
        iterations_dir.mkdir(parents=True, exist_ok=True)
        iterations_path = iterations_dir / "iterations.json"
        iterations_path.write_text(
            json.dumps(materialized_iterations, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        written.append(str(iterations_path))

    top = context.contract.get("top_entity") or {}
    top_class_iri = str(top.get("class_iri") or "").strip()
    if not top_class_iri:
        # Legacy meta-task compatibility only. The two-input domain-config path
        # always supplies a GPT-5-selected top entity before support generation.
        iter1 = context.pipeline_runtime_policies.get("iter1_top_entity_kg") or {}
        configured_local = str(
            (iter1.get("prompt_rules") or {}).get("top_level_entity_name") or ""
        ).strip()
        configured_class = (context.parsed.get("classes") or {}).get(
            configured_local
        ) or {}
        top_class_iri = str(configured_class.get("iri") or "").strip()
    if top_class_iri:
        sparql_dir = Path(context.output_root) / "sparqls" / context.ontology.name
        sparql_dir.mkdir(parents=True, exist_ok=True)
        sparql_path = sparql_dir / "top_entity_parsing.sparql"
        sparql_path.write_text(
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n\n"
            "SELECT DISTINCT ?entity ?label WHERE {\n"
            f"  ?entity a <{top_class_iri}> .\n"
            "  OPTIONAL { ?entity rdfs:label ?label }\n"
            "}\n",
            encoding="utf-8",
        )
        written.append(str(sparql_path))
    from src.agents.scripts_and_prompts_generation.enrichment_target_sparql import (
        write_enrichment_target_sparql,
    )

    enrichment_sparql = write_enrichment_target_sparql(context)
    if enrichment_sparql:
        written.append(enrichment_sparql)
    return written


def generate_deterministic_prompt_slice(context: AgenticGenerationContext) -> list[str]:
    prompts_dir = Path(context.prompts_dir)
    prompts_dir.mkdir(parents=True, exist_ok=True)
    files = (
        {}
        if context.ontology.role == "extension"
        else {
            "EXTRACTION_ITER_1.md": _extraction_prompt(context),
            "KG_BUILDING_ITER_1.md": _kg_prompt(context),
        }
    )
    iterations = _iteration_plan(context)
    for iteration in iterations.get("iterations") or []:
        iter_num = iteration.get("iteration_number")
        files[f"EXTRACTION_ITER_{iter_num}.md"] = _iteration_extraction_prompt(
            context, iteration
        )
        files[f"KG_BUILDING_ITER_{iter_num}.md"] = _iteration_kg_prompt(
            context, iteration
        )
        if context.ontology.role != "extension" and int(iter_num) >= 2:
            files[f"KG_BUILDING_ITER_{iter_num}_ONEPASS.md"] = (
                _iteration_kg_onepass_prompt(context, iteration)
            )
        if iteration.get("has_pre_extraction"):
            files[f"PRE_EXTRACTION_ITER_{iter_num}.md"] = _pre_extraction_prompt(
                context, iteration
            )
        for sub_iteration in iteration.get("sub_iterations") or []:
            if not isinstance(sub_iteration, dict):
                continue
            sub_num = str(sub_iteration.get("iteration_number") or "").replace(".", "_")
            if sub_num:
                files[f"EXTRACTION_ITER_{sub_num}.md"] = (
                    _sub_iteration_extraction_prompt(
                        context,
                        iteration,
                        sub_iteration,
                    )
                )
    written: list[str] = []
    for name, content in files.items():
        path = prompts_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(str(path))
    written.extend(generate_runtime_support_slice(context, iterations=iterations))
    for name in files:
        path = prompts_dir / name
        component = _write_materializable_prompt_component(context, path)
        if component is not None:
            written.append(str(component))
    return written


def inject_repair_exercise_defects(context: AgenticGenerationContext) -> list[str]:
    """Introduce realistic isolated defects so the validation/repair loop is observable."""
    changed: list[str] = []
    prompts_dir = Path(context.prompts_dir)
    kg_prompt = prompts_dir / "KG_BUILDING_ITER_1.md"
    if kg_prompt.exists():
        text = kg_prompt.read_text(encoding="utf-8")
        text += "\n\nTODO PLACEHOLDER: seeded invalid cross-ontology residue for repair-loop exercise.\n"
        kg_prompt.write_text(text, encoding="utf-8")
        changed.append(str(kg_prompt))

    scripts_dir = Path(context.scripts_dir)
    relationships = (
        scripts_dir / f"{_py_name(context.ontology.name)}_creation_relationships.py"
    )
    if relationships.exists():
        text = relationships.read_text(encoding="utf-8")
        first_rel = re.search(
            r"\ndef add_[A-Za-z0-9_]+\(subject_iri: str, object_iri: str\) -> str:\n(?:    .*\n)+?\n",
            text,
        )
        if first_rel:
            relationships.write_text(
                text.replace(first_rel.group(0), "\n", 1), encoding="utf-8"
            )
            changed.append(str(relationships))
    return changed


def run_agentic_generation_experiment(
    ontology_names: list[str],
    *,
    meta_task_config_path: str | Path | None = None,
    domain_config_path: str | Path | None = None,
    output_root: str | Path = "ai_generated_contents_agentic_candidate",
    generate_scripts: bool = False,
    generate_prompts: bool = False,
    repair_loop: bool = False,
    exercise_repair: bool = False,
    max_repair_iterations: int = 3,
    llm_agent_generation: bool = False,
    generation_model: str = "gpt-5.2",
    max_agent_rounds: int = 2,
    repair_only: bool = False,
    generation_only: bool = False,
    package_synthesis: bool = False,
    runtime_adapter_synthesis: bool = False,
    creation_foundation_synthesis: bool = False,
    creation_foundation_module: str | None = None,
    focused_repair: bool = False,
    incremental_generation_repair: bool = False,
    max_focus_targets: int = 3,
    focused_package_integration: bool = False,
    parallel_generation: bool = True,
    max_generation_workers: int = 5,
    edit_backend: str = "exact_edits",
    target_artifacts: list[str] | None = None,
    write_context_files: bool = True,
    prompt_enhancement: bool = False,
    prompt_enhancement_fixture: str | Path | None = None,
    max_prompt_enhancement_rounds: int = 2,
    prompt_enhancement_evaluation_repeats: int = 1,
    prompt_enhancement_resume_case: str | Path | None = None,
    operation_mode: str = "legacy",
) -> dict[str, Any]:
    output_path = Path(output_root)
    targeted_snapshot = (
        {
            path: path.read_bytes()
            for path in output_path.rglob("*")
            if path.is_file()
        }
        if target_artifacts and output_path.is_dir()
        else {}
    )
    targeted_existing_paths = set(targeted_snapshot)

    planner = None
    if domain_config_path is not None and target_artifacts:
        ontology_name = ontology_names[0] if len(ontology_names) == 1 else ""
        compiled_plan_path = output_path / "iterations" / ontology_name / "iterations.json"
        contract_path = (
            output_path
            / "ontology_structures"
            / ontology_name
            / "generation_contract.json"
        )
        if compiled_plan_path.is_file() and contract_path.is_file():
            compiled_plan = json.loads(
                compiled_plan_path.read_text(encoding="utf-8")
            )
            compiled_contract = json.loads(contract_path.read_text(encoding="utf-8"))
            assignments: list[dict[str, Any]] = []
            enrichment_focus: dict[str, str] = {}
            for iteration in compiled_plan.get("iterations") or []:
                if not isinstance(iteration, dict):
                    continue
                iteration_number = str(iteration.get("iteration_number") or "")
                responsibilities = iteration.get("responsibilities") or {}
                assignments.append(
                    {
                        "slot": f"iter{iteration_number}",
                        "classes": list(responsibilities.get("classes") or []),
                        "object_properties": list(
                            responsibilities.get("object_properties") or []
                        ),
                        "rationale": str(iteration.get("description") or ""),
                    }
                )
                for sub_iteration in iteration.get("sub_iterations") or []:
                    if isinstance(sub_iteration, dict):
                        sub_number = str(
                            sub_iteration.get("iteration_number") or ""
                        )
                        enrichment_focus[f"iter{sub_number}"] = str(
                            sub_iteration.get("description") or ""
                        )
            top_entity = dict(compiled_contract.get("top_entity") or {})
            publish_contract = compiled_contract.get("ontology_publish_contract") or {}
            extension_focus = publish_contract.get("extension_focus") or {}
            if top_entity.get("owned_by_extension") is False and extension_focus:
                top_entity = dict(extension_focus)
            top_entity.setdefault("status", "known")
            top_entity.setdefault("source", "existing_compiled_contract")
            top_entity.setdefault("model", "compiled")
            top_entity.setdefault(
                "rationale", "Preserved from the existing compiled generation contract."
            )
            top_entity.setdefault(
                "evidence", [str(top_entity.get("class_local") or "")]
            )
            planner_answers = iter(
                [
                    top_entity,
                    {
                        "assignments": assignments,
                        "enrichment_focus": enrichment_focus,
                    },
                ]
            )

            def planner(_model: str, _prompt: str) -> dict[str, Any]:
                return next(planner_answers)

    if domain_config_path is not None:
        if len(ontology_names) != 1:
            raise ValueError("domain_config_path requires exactly one ontology")
        contexts = [
            build_domain_generation_context(
                domain_config_path=domain_config_path,
                output_root=output_root,
                repository_root=Path.cwd(),
                write_files=write_context_files,
                planner=planner,
                operation_mode=operation_mode,
            )
        ]
        if contexts[0].ontology.name != ontology_names[0]:
            raise ValueError(
                "domain config ontology does not match requested ontology: "
                f"{contexts[0].ontology.name!r} != {ontology_names[0]!r}"
            )
    else:
        contexts = build_contexts_for_ontologies(
            ontology_names,
            meta_task_config_path=meta_task_config_path,
            output_root=output_root,
            write_files=write_context_files,
        )
    if targeted_snapshot:
        for path in [
            candidate
            for candidate in output_path.rglob("*")
            if candidate.is_file() and candidate not in targeted_existing_paths
        ]:
            path.unlink()
        for path, content in targeted_snapshot.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    all_contracts = [ctx.contract for ctx in contexts]
    reports = []
    for context in contexts:
        requested_target_names = {
            Path(item).name for item in (target_artifacts or [])
        }
        protected_artifacts: dict[Path, bytes] = {}
        if requested_target_names:
            for artifact_dir, suffix in (
                (Path(context.scripts_dir), ".py"),
                (Path(context.prompts_dir), ".md"),
            ):
                if not artifact_dir.is_dir():
                    continue
                for artifact in artifact_dir.glob(f"*{suffix}"):
                    if artifact.name not in requested_target_names:
                        protected_artifacts[artifact] = artifact.read_bytes()
            prompts_dir = Path(context.prompts_dir)
            if prompts_dir.is_dir():
                for component in prompts_dir.glob("*.materializable.inc"):
                    prompt_name = component.name.removesuffix(
                        ".materializable.inc"
                    ) + ".md"
                    if prompt_name not in requested_target_names:
                        protected_artifacts[component] = component.read_bytes()
        resumable_artifacts = (
            _resumable_artifact_snapshots(context, requested_target_names)
            if llm_agent_generation and hasattr(context, "output_root")
            else {}
        )
        written: list[str] = []
        if generate_scripts and not repair_only:
            written.extend(generate_deterministic_script_slice(context))
        if generate_prompts and not repair_only:
            written.extend(generate_deterministic_prompt_slice(context))
        for resumable_path, resumable_content in resumable_artifacts.items():
            resumable_path.parent.mkdir(parents=True, exist_ok=True)
            resumable_path.write_bytes(resumable_content)
        for protected_path, protected_content in protected_artifacts.items():
            protected_path.write_bytes(protected_content)
        if llm_agent_generation and not repair_only:
            # Deterministic generation establishes only the required artifact slots.
            # Remove its semantic content so every final line is authored by the LLM.
            for raw_path in written:
                path = Path(raw_path)
                if (
                    path.is_file()
                    and path.suffix in {".py", ".md"}
                    and path not in resumable_artifacts
                    and (
                        not requested_target_names
                        or path.name in requested_target_names
                    )
                ):
                    path.write_text("", encoding="utf-8")
        exercise_defects: list[str] = []
        if exercise_repair:
            exercise_defects = inject_repair_exercise_defects(context)
        foreign = [
            bundle
            for bundle in all_contracts
            if bundle.get("ontology_name") != context.ontology.name
        ]
        repair_history: list[dict[str, Any]] = []
        llm_agent_run: dict[str, Any] | None = None
        if llm_agent_generation and (generate_scripts or generate_prompts):
            llm_agent_run = run_pure_llm_generation_rounds(
                context,
                model_name=generation_model,
                foreign_contracts=foreign,
                max_rounds=max_agent_rounds,
                generate_scripts=generate_scripts,
                generate_prompts=generate_prompts,
                repair_only=repair_only,
                generation_only=generation_only,
                package_synthesis=package_synthesis,
                runtime_adapter_synthesis=runtime_adapter_synthesis,
                creation_foundation_synthesis=creation_foundation_synthesis,
                creation_foundation_module=creation_foundation_module,
                focused_repair=focused_repair,
                incremental_generation_repair=incremental_generation_repair,
                max_focus_targets=max_focus_targets,
                focused_package_integration=focused_package_integration,
                parallel_generation=parallel_generation,
                max_generation_workers=max_generation_workers,
                edit_backend=edit_backend,
                target_artifacts=target_artifacts,
                protected_artifacts=protected_artifacts,
            )
            final_report = llm_agent_run.get("final_report")
            report = (
                dict(final_report)
                if isinstance(final_report, dict)
                else build_validation_report(
                    context, foreign_contracts=foreign, write_report=True
                )
            )
        else:
            report = build_validation_report(
                context, foreign_contracts=foreign, write_report=True
            )
        repair_history.append(
            {
                "iteration": 0,
                "ok": report.get("ok"),
                "failures": report.get("failures") or [],
                "feedback": report.get("feedback") or {},
                "repaired_files": [],
            }
        )
        if repair_loop and not llm_agent_generation and not report.get("ok"):
            repair_history.append(
                {
                    "iteration": 1,
                    "ok": False,
                    "failures": [
                        "scripted_repair_disabled: enable llm_agent_generation for LLM repair"
                    ],
                    "feedback": report.get("feedback") or {},
                    "repaired_files": [],
                }
            )
        report["written_files"] = written
        report["exercise_defects"] = exercise_defects
        report["generation_mode"] = (
            "focused_package_integration"
            if focused_package_integration or package_synthesis
            else "pure_llm_repair_only"
            if repair_only
            else (
                "pure_llm_generation_checkpoint"
                if generation_only
                else "pure_llm_unified_diff"
                if llm_agent_generation
                else "deterministic_scaffold"
            )
        )
        report["llm_agent_run"] = llm_agent_run
        report["repair_history"] = repair_history
        if prompt_enhancement:
            if not report.get("ok"):
                report["prompt_enhancement"] = {
                    "ok": False,
                    "status": "blocked",
                    "reason": "generation validation failed",
                }
            elif prompt_enhancement_fixture is None:
                raise ValueError("prompt enhancement requires a fixture")
            else:
                from src.agents.scripts_and_prompts_generation.prompt_enhancement_pipeline import (
                    run_formal_prompt_enhancement,
                )

                report["prompt_enhancement"] = run_formal_prompt_enhancement(
                    context=context,
                    fixture_path=Path(prompt_enhancement_fixture),
                    model=generation_model,
                    max_rounds=max_prompt_enhancement_rounds,
                    evaluation_repeats=prompt_enhancement_evaluation_repeats,
                    resume_case_dir=(
                        Path(prompt_enhancement_resume_case)
                        if prompt_enhancement_resume_case is not None
                        else None
                    ),
                )
                report["ok"] = bool(report["prompt_enhancement"].get("ok"))
        reports.append(report)

    summary = {
        "ok": all(report.get("ok") for report in reports),
        "output_root": str(output_root),
        "reports": reports,
    }
    root = Path(output_root)
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "reports" / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary
