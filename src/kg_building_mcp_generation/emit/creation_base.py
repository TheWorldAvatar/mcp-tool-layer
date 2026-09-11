"""Emit *_creation_base.py."""

from __future__ import annotations

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.compile.reuse_policy import (
    existing_entity_check_contracts,
)
from src.kg_building_mcp_generation.emit.creation_helpers import (
    _namespace_uri,
    _ordering_datatype_properties,
    _py_name,
)

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

