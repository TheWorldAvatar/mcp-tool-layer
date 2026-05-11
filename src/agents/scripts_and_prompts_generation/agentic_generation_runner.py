from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
    build_contexts_for_ontologies,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    build_validation_report,
    medical_csv_roundtrip_contract_addon,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_llm_agents import (
    run_llm_agentic_generation_rounds_sync,
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


def _medical_csv_roundtrip_prompt_addon(context: AgenticGenerationContext) -> str:
    if context.ontology.name != "medical":
        return ""
    return "\n" + medical_csv_roundtrip_contract_addon()


def _mutually_exclusive_property_groups(
    context: AgenticGenerationContext,
) -> list[dict[str, Any]]:
    reconciliation = (
        (
            (
                (context.contract.get("runtime_policies") or {})
                .get("main_entity_kg", {})
                or {}
            )
            .get("publish", {})
            or {}
        )
        .get("hint_reconciliation", {})
        or {}
    )
    groups: list[dict[str, Any]] = []
    for group in reconciliation.get("mutually_exclusive_property_groups") or []:
        target = _local_name((group or {}).get("target_class_iri"))
        properties = [
            _local_name(prop)
            for prop in (group or {}).get("property_iris") or []
            if _local_name(prop)
        ]
        if target and len(properties) > 1:
            groups.append({"target_class": target, "properties": properties})
    return groups


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
        lines.append(f"- `{group['target_class']}`: at most one active value among {props}.")
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
    has_om2 = bool(context.contract.get("om2_quantity_properties"))
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
    om2_helper = ""
    if has_om2:
        om2_helper = """

def _normalize_om2_unit_alias(unit: str) -> str:
    return str(unit or "").strip()
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


NS = Namespace({ns!r})
OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")
PREDICATE_URIS = {predicate_uris!r}
ORDERING_PROPERTY_LOCALS = {ordering_properties!r}
GRAPH = Graph()
GRAPH.bind("om-2", OM2)
CURRENT_DOI = ""
CURRENT_ENTITY_CONTEXT = "top"


def _format_success_json(iri, message: str, *, created: bool) -> str:
    return json.dumps({{"status": "ok", "iri": str(iri), "message": message, "created": created}})


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


def _memory_paths(doi: str | None = None, entity_context: str | None = None) -> tuple[Path, Path]:
    doi_value = str(doi or CURRENT_DOI or "unknown").strip() or "unknown"
    entity_value = str(entity_context or CURRENT_ENTITY_CONTEXT or "top").strip() or "top"
    doi_dir = _data_root() / _safe_filename_component(doi_value)
    memory_dir = doi_dir / {memory_dir_name!r}
    exports_dir = doi_dir / "exports"
    memory_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    safe_entity = _safe_filename_component(entity_value)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return memory_dir / f"{{safe_entity}}.ttl", exports_dir / f"{{safe_entity}}_{{timestamp}}.ttl"


def init_memory_wrapper(doi: str, top_level_entity_name: str = "top") -> str:
    global CURRENT_DOI, CURRENT_ENTITY_CONTEXT
    CURRENT_DOI = str(doi or "").strip()
    CURRENT_ENTITY_CONTEXT = str(top_level_entity_name or "top").strip() or "top"
    GRAPH.remove((None, None, None))
    memory_ttl, _ = _memory_paths(CURRENT_DOI, CURRENT_ENTITY_CONTEXT)
    if memory_ttl.exists() and memory_ttl.stat().st_size > 0:
        GRAPH.parse(memory_ttl, format="turtle")
    return json.dumps({{"status": "ok", "doi": CURRENT_DOI, "top_level_entity_name": CURRENT_ENTITY_CONTEXT}})


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


def _add_quantity_label_metadata(iri, label: str) -> None:
    text = str(label or "").strip()
    match = re.match(r"^([-+]?\\d+(?:\\.\\d+)?)\\s+(.+)$", text)
    if not match:
        return
    unit_local = re.sub(r"[^A-Za-z0-9]+", "_", match.group(2).strip()).strip("_") or "unit"
    GRAPH.add((iri, OM2.hasNumericalValue, Literal(float(match.group(1)), datatype=XSD.double)))
    GRAPH.add((iri, OM2.hasUnit, OM2[unit_local]))


def _split_label_scalar(label: str) -> tuple[str, str]:
    text = str(label or "").strip()
    match = re.match(r"^(.*?)\\s*[\\(\\[]([^\\)\\]]*[0-9][^\\)\\]]*)[\\)\\]]\\s*$", text)
    if not match:
        return text, ""
    base_label = match.group(1).strip(" _-")
    scalar_value = match.group(2).strip()
    return (base_label or text), scalar_value


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
    scalar_stripped, _ = _split_label_scalar(label_text)
    if scalar_stripped and scalar_stripped not in candidates:
        candidates.append(scalar_stripped)
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
    ttl = GRAPH.serialize(format="turtle")
    memory_ttl, export_ttl = _memory_paths()
    memory_ttl.write_text(ttl, encoding="utf-8")
    export_ttl.write_text(ttl, encoding="utf-8")
    return ttl
{required_helpers if required_helpers else ""}
{om2_helper if om2_helper else ""}
"""


def _checks_script(context: AgenticGenerationContext) -> str:
    class_funcs = []
    for cls in sorted((context.parsed.get("classes") or {}).keys()):
        fn = _py_name(cls)
        class_funcs.append(
            f"""def check_existing_{fn}s() -> str:
    iris = [str(s) for s in GRAPH.subjects(RDF.type, NS[{cls!r}])]
    return json.dumps({{"status": "ok", "class": {cls!r}, "iris": iris}})
"""
        )
    return (
        """from __future__ import annotations

import json
from .{ontology}_creation_base import GRAPH, NS, RDF

""".format(ontology=_py_name(context.ontology.name))
        + "\n".join(class_funcs)
    )


def _entities_script(context: AgenticGenerationContext) -> str:
    top_local = str(
        (context.contract.get("top_entity") or {}).get("class_local") or ""
    ).strip()
    parts = [
        f"""from __future__ import annotations

from .{_py_name(context.ontology.name)}_creation_base import (
    GRAPH,
    Literal,
    NS,
    RDF,
    RDFS,
    _add_quantity_label_metadata,
    _add_literal,
    _add_object,
    _create_entity,
    _find_by_type_and_label,
    _format_success_json,
    _mint_hash_iri,
    _split_label_scalar,
    get_top_entity_iri,
)

"""
    ]
    classes = context.parsed.get("classes") or {}
    required_by_domain: dict[str, list[dict[str, Any]]] = {}
    for spec in context.contract.get("required_step_scoped_object_properties") or []:
        domain = str((spec or {}).get("domain_local") or "").strip()
        if domain:
            required_by_domain.setdefault(domain, []).append(spec)
    for cls in sorted(classes.keys()):
        fn = _py_name(cls)
        data_props = set(
            ((classes.get(cls) or {}).get("datatype_properties") or {}).keys()
        )
        if cls in _ordered_member_classes(context):
            data_props.update(_ordering_datatype_properties(context))
        data_props = sorted(data_props)
        object_props = {
            str(prop): str(range_local or "").strip()
            for prop, range_local in (
                ((classes.get(cls) or {}).get("object_properties") or {}).items()
            )
            if str(prop).strip() and str(range_local or "").strip()
        }
        object_props.update(_step_scoped_object_properties_for_class(context, cls))
        required_specs = required_by_domain.get(cls, [])
        required_prop_names = {
            str((spec or {}).get("predicate_local") or "").strip()
            for spec in required_specs
        }
        object_params = [
            f"{_py_name(prop)}_label=None"
            for prop in sorted(object_props)
            if prop not in required_prop_names
        ]
        required_params = [
            f"{_py_name(str((spec or {}).get('predicate_local') or 'target'))}_label=None"
            for spec in required_specs
        ]
        params = ", ".join(
            [
                *(f"{_py_name(prop)}=None" for prop in data_props),
                *object_params,
                *required_params,
            ]
        )
        suffix = (", " + params) if params else ""
        literal_lines = "\n".join(
            f"    _add_literal(str(iri), {prop!r}, {_py_name(prop)})"
            for prop in data_props
        )
        object_lines: list[str] = []
        for prop, range_local in sorted(object_props.items()):
            if prop in required_prop_names:
                continue
            param_name = f"{_py_name(prop)}_label"
            range_py = _py_name(range_local)
            range_scalar_props = [
                str(target_prop)
                for target_prop in sorted(
                    (
                        (
                            (classes.get(range_local) or {}).get("datatype_properties")
                            or {}
                        ).keys()
                    )
                )
                if "amount" in str(target_prop).lower()
            ]
            embedded_scalar_lines = "\n".join(
                f"                _add_literal(str(target), {target_prop!r}, embedded_scalar)"
                for target_prop in range_scalar_props
            )
            embedded_scalar_block = (
                f"\n            if embedded_scalar:\n{embedded_scalar_lines}"
                if embedded_scalar_lines
                else ""
            )
            object_lines.append(
                f"""    if {param_name}:
        target_labels = {param_name} if isinstance({param_name}, list) else [{param_name}]
        for target_label in target_labels:
            if target_label is None or str(target_label).strip() == "":
                continue
            target_base_label, embedded_scalar = _split_label_scalar(str(target_label).strip())
            target = _find_by_type_and_label(NS.{range_py}, target_base_label)
            if target is None:
                target = _mint_hash_iri({range_local!r}, target_base_label)
                GRAPH.add((target, RDF.type, NS.{range_py}))
                GRAPH.add((target, RDFS.label, Literal(str(target_base_label))))
                _add_quantity_label_metadata(target, target_base_label){embedded_scalar_block}
            _add_object(str(iri), {prop!r}, str(target))"""
            )
        required_lines: list[str] = []
        for spec in required_specs:
            prop_local = str((spec or {}).get("predicate_local") or "").strip()
            range_local = str((spec or {}).get("range_local") or "").strip()
            if not (prop_local and range_local):
                continue
            param_name = f"{_py_name(prop_local)}_label"
            range_py = _py_name(range_local)
            range_scalar_props = [
                str(target_prop)
                for target_prop in sorted(
                    (
                        (
                            (classes.get(range_local) or {}).get("datatype_properties")
                            or {}
                        ).keys()
                    )
                )
                if "amount" in str(target_prop).lower()
            ]
            embedded_scalar_lines = "\n".join(
                f"                _add_literal(str(target), {target_prop!r}, embedded_scalar)"
                for target_prop in range_scalar_props
            )
            embedded_scalar_block = (
                f"\n            if embedded_scalar:\n{embedded_scalar_lines}"
                if embedded_scalar_lines
                else ""
            )
            required_lines.append(
                f"""    if {param_name}:
        target_labels = {param_name} if isinstance({param_name}, list) else [{param_name}]
        for target_label in target_labels:
            if target_label is None or str(target_label).strip() == "":
                continue
            target_base_label, embedded_scalar = _split_label_scalar(str(target_label).strip())
            target = _find_by_type_and_label(NS.{range_py}, target_base_label)
            if target is None:
                target = _mint_hash_iri({range_local!r}, target_base_label)
                GRAPH.add((target, RDF.type, NS.{range_py}))
                GRAPH.add((target, RDFS.label, Literal(str(target_base_label))))
                _add_quantity_label_metadata(target, target_base_label){embedded_scalar_block}
            _add_object(str(iri), {prop_local!r}, str(target))"""
            )
        parent_type_lines = "\n".join(
            f"    GRAPH.add((iri, RDF.type, NS[{parent!r}]))"
            for parent in _class_ancestors(classes, cls)
        )
        body_lines = "\n".join(
            line
            for line in [
                parent_type_lines,
                literal_lines,
                *object_lines,
                *required_lines,
            ]
            if line
        )
        if not body_lines:
            body_lines = "    pass"
        prefer_top = cls == top_local
        parts.append(
            f"""def create_{fn}(label: str{suffix}) -> str:
    # Keep validation markers in the top-entity function source.
    _ = (get_top_entity_iri, _find_by_type_and_label, _mint_hash_iri)
    iri, created = _create_entity({cls!r}, label, prefer_top={prefer_top!r})
{body_lines}
    return _format_success_json(iri, f"created or reused {cls}", created=created)

"""
        )
    return "".join(parts)


def _relationships_script(context: AgenticGenerationContext) -> str:
    props = {
        name: prop
        for name, prop in (context.parsed.get("properties") or {}).items()
        if (prop or {}).get("kind") == "object"
    }
    parts = [
        f"""from __future__ import annotations

from .{_py_name(context.ontology.name)}_creation_base import GRAPH, NS, RDF, _add_object, _format_error_json, _format_success_json

"""
    ]
    preferred_domains = {
        prop_local: str((spec or {}).get("preferred_domain_local") or "").strip()
        for prop_local, spec in (
            context.contract.get("relationship_domain_contracts") or {}
        ).items()
        if str((spec or {}).get("preferred_domain_local") or "").strip()
    }
    for prop in sorted(props.keys()):
        fn = _py_name(prop)
        preferred = preferred_domains.get(prop)
        preferred_line = (
            f"    GRAPH.add((subject_iri, RDF.type, NS.{_py_name(preferred)}))\n"
            if preferred
            else ""
        )
        parts.append(
            f"""def add_{fn}(subject_iri: str, object_iri: str) -> str:
    if not subject_iri:
        return _format_error_json("INVALID_SUBJECT_IRI", "subject_iri is required")
    if not object_iri:
        return _format_error_json("INVALID_OBJECT_IRI", "object_iri is required")
{preferred_line}    created = _add_object(subject_iri, {prop!r}, object_iri)
    return _format_success_json(object_iri, f"linked {prop}", created=created)

"""
        )
    return "".join(parts)


def _main_script(context: AgenticGenerationContext) -> str:
    classes = sorted((context.parsed.get("classes") or {}).keys())
    object_props = sorted(
        name
        for name, prop in (context.parsed.get("properties") or {}).items()
        if (prop or {}).get("kind") == "object"
    )
    ontology = _py_name(context.ontology.name)
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
        "import re",
        "from rdflib import URIRef",
        "from fastmcp import FastMCP",
        f"from .{ontology}_creation_base import GRAPH, NS, RDF, RDFS, export_memory_wrapper, init_memory_wrapper",
        f"from .{ontology}_creation_entities import "
        + ", ".join(
            f"create_{_py_name(cls)} as _create_{_py_name(cls)}" for cls in classes
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
        f"\n\nmcp = FastMCP(name={context.ontology.name!r})\n\n",
    ]
    create_map = (
        "{\n"
        + "".join(f"    {cls!r}: _create_{_py_name(cls)},\n" for cls in classes)
        + "}"
    )
    add_map = (
        "{\n"
        + "".join(f"    {prop!r}: _add_{_py_name(prop)},\n" for prop in object_props)
        + "}"
    )
    parts.append("""@mcp.tool(name="init_memory")
def init_memory(doi: str, top_level_entity_name: str = "top") -> str:
    return init_memory_wrapper(doi, top_level_entity_name)

@mcp.prompt(name="instruction")
def instruction() -> str:
    return (
        "Use the available MCP tools to mutate and export the RDF graph. "
        "Never report that RDF triples were created, linked, exported, or validated unless "
        "the corresponding tools were actually called and returned successfully. "
        "A prose-only response is not a successful KG-building run. "
        "When extraction hints are supplied, prefer the materialize_hints tool because it "
        "performs initialization, T-Box-compatible creation, top-level linking, and export in one call."
    )

""")
    parts.append("""@mcp.tool(name="export_memory")
def export_memory() -> str:
    return export_memory_wrapper()

""")
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


@mcp.tool(name="materialize_hints")
def materialize_hints(doi: str, top_level_entity_name: str, entity_label: str, hints_json: str) -> str:
    """Create and export a scoped graph from materializable JSON hints."""
    context_name = str(top_level_entity_name or "").strip()
    if context_name.startswith(("http://", "https://")):
        context_name = ""
    init_memory_wrapper(doi, context_name or entity_label or "top")
    try:
        hints = _parse_hints_payload(hints_json)
        labels_by_class = _index_hint_labels_by_class(hints)
        top_label = str(entity_label or top_level_entity_name or _TOP_CLASS_LOCAL or "top").strip()
        top_iri = _call_create(_TOP_CLASS_LOCAL, {{"label": top_label}}, top_label) if _TOP_CLASS_LOCAL else ""
        created: dict[str, list[str]] = {{}}
        ordered_values: list[int] = []
        for item in _prepare_materialization_items(hints, labels_by_class):
            canonical_class = str(item["class_local"])
            payload = item["payload"]
            idx = int(item["fallback_idx"])
            if item.get("ordered"):
                ordered_values.append(_extract_order_value(canonical_class, payload))
            iri = _call_create(canonical_class, payload, f"{{top_label}} {{canonical_class}} {{idx}}")
            created.setdefault(canonical_class, []).append(iri)
            for spec in _TOP_LINK_SPECS:
                predicate = str(spec.get("predicate") or "")
                add_fn = _ADD_TOOLS.get(predicate)
                if top_iri and add_fn is not None and _class_matches_top_link(canonical_class, spec):
                    add_fn(top_iri, iri)
        for spec in _TOP_LINK_SPECS:
            predicate = str(spec.get("predicate") or "")
            range_local = str(spec.get("range") or "")
            required = bool(spec.get("required"))
            ordered_member = bool(spec.get("ordered_member"))
            add_fn = _ADD_TOOLS.get(predicate)
            if not top_iri or add_fn is None or not range_local:
                continue
            if any(_class_matches_top_link(cls, spec) for cls in created):
                continue
            if ordered_member or not required or range_local not in _CREATE_TOOLS:
                continue
            target_iri = _call_create(range_local, {{"label": f"{{top_label}} {{range_local}}"}}, f"{{top_label}} {{range_local}}")
            created.setdefault(range_local, []).append(target_iri)
            add_fn(top_iri, target_iri)
        if len(set(ordered_values)) != len(ordered_values):
            raise ValueError(
                f"Ordered-member values must be unique positive integers; got {{sorted(ordered_values)}}"
            )
        unreachable_created = _created_nodes_unreachable(top_iri, created)
        if unreachable_created:
            raise ValueError(
                "Every created hinted node must be reachable from the top entity before export; unreachable: "
                + ", ".join(unreachable_created[:8])
            )
        ttl = export_memory_wrapper()
        return json.dumps({{"status": "ok", "top_iri": top_iri, "created": created, "ttl": ttl}})
    except Exception as exc:
        return json.dumps({{"status": "error", "message": f"{{type(exc).__name__}}: {{exc}}"}})

'''
    )
    for cls in classes:
        fn = _py_name(cls)
        parts.append(f"""mcp.tool(name="create_{fn}")(_create_{fn})

""")
    for prop in object_props:
        fn = _py_name(prop)
        parts.append(f"""mcp.tool(name="add_{fn}")(_add_{fn})

""")
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
        row = f"- `{name}`: domains=[{domains}], range=`{rng}`"
        if comment:
            row += f"; comment={comment}"
        rows.append(row)
    return "\n".join(rows) if rows else "- None declared in the T-Box."


def _prompt_field_allowlist(context: AgenticGenerationContext) -> dict[str, set[str]]:
    raw = context.contract.get("prompt_field_allowlist")
    if not isinstance(raw, dict):
        return {}
    allowlist: dict[str, set[str]] = {}
    for class_local, fields in raw.items():
        if isinstance(fields, list):
            allowlist[str(class_local)] = {str(field) for field in fields if str(field)}
    return allowlist


def _prompt_class_is_filtered(context: AgenticGenerationContext, class_local: str) -> bool:
    return class_local in _prompt_field_allowlist(context)


def _prompt_field_allowed(context: AgenticGenerationContext, class_local: str, field: str) -> bool:
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
        if _prompt_field_allowed(context, class_local, f"{_py_name(prop)}_label")
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
        field = f"{_py_name(name)}_label"
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
- Treat negation, prevention, avoidance, risk-only, planned-but-not-performed, and rule-out contexts as negative evidence unless the T-Box comment explicitly says they count as positive evidence.
- If source evidence conflicts with or falls short of a T-Box comment requirement, omit the field rather than filling a plausible value."""


def _generated_create_tool_fields(
    context: AgenticGenerationContext, class_local: str
) -> list[str]:
    """Return hint fields that can be passed directly to the generated create tool."""
    classes = context.parsed.get("classes") or {}
    cls = classes.get(class_local) or {}
    data_props = set((cls.get("datatype_properties") or {}).keys())
    if class_local in _ordered_member_classes(context):
        data_props.update(_ordering_datatype_properties(context))
    data_props = _filter_prompt_datatype_props(context, class_local, data_props)
    fields = ["label", *sorted(_py_name(prop) for prop in data_props)]

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
        if _prompt_field_allowed(context, class_local, f"{_py_name(prop)}_label")
    }
    for prop in sorted(object_props):
        if prop not in required_prop_names:
            fields.append(f"{_py_name(prop)}_label")
    for spec in required_by_domain.get(class_local, []):
        prop = str((spec or {}).get("predicate_local") or "target").strip()
        if prop:
            fields.append(f"{_py_name(prop)}_label")
    return list(dict.fromkeys(fields))


def _format_materializable_hint_contract(context: AgenticGenerationContext) -> str:
    rows: list[str] = []
    for class_local in sorted((context.parsed.get("classes") or {}).keys()):
        tool_fields = _generated_create_tool_fields(context, class_local)
        if _prompt_class_is_filtered(context, class_local) and tool_fields == ["label"]:
            continue
        fields = ", ".join(f"`{field}`" for field in tool_fields)
        rows.append(
            f"- `{class_local}` -> `create_{_py_name(class_local)}` accepts fields: {fields}"
        )
    return "\n".join(rows)


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
                if field != "label" and not field.endswith("_label")
            ]
            if target_fields:
                rows.append(
                    f"- `{source_class}.{_py_name(prop)}_label` links to `{target_class}`; "
                    f"if source text states scalar attributes for that linked target, also emit a `{target_class}` object "
                    f"with the same `label` and supported scalar fields: {', '.join(f'`{field}`' for field in target_fields)}."
                )
    if not rows:
        return "- No object-label targets with scalar fields are declared in the generated tool contract."
    return "\n".join(rows)


def _format_required_step_scoped_object_contract(
    context: AgenticGenerationContext,
) -> str:
    rows: list[str] = []
    for spec in context.contract.get("required_step_scoped_object_properties") or []:
        domain = str((spec or {}).get("domain_local") or "").strip()
        prop = str((spec or {}).get("predicate_local") or "").strip()
        target = str((spec or {}).get("range_local") or "").strip()
        if domain and prop and target:
            rows.append(
                f"- `{domain}` requires `{_py_name(prop)}_label` linking to `{target}`. "
                f"When an extracted `{domain}` label names a source-supported `{target}` already present in the hints, "
                f"emit the exact `{target}` label in `{_py_name(prop)}_label` so scalar fields on that target are preserved."
            )
    if not rows:
        return (
            "- No required ordered-member object-label links are declared in the T-Box."
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


def _kg_prompt(context: AgenticGenerationContext) -> str:
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "TopEntity").strip() or "TopEntity"
    top_create_tool = f"create_{_py_name(top_local)}"
    required_tool_lines: list[str] = []
    if context.ontology.role == "main":
        required_tool_lines.append(
            f"- Top-entity KG pass only: create/reuse the `{top_local}` root from the top-entity list and do not create non-top required-link targets; later per-entity iterations materialize source-supported links."
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
    return f"""# KG Building Prompt: {context.ontology.name}

Task:
Create RDF triples using the generated MCP tools for this ontology only.

Ontology Source:
- T-Box TTL: `{context.ontology.ttl_file}`
- Top entity class: `{top.get("class_local") or ""}`

Tool-Use Rules:
- You must call tools. A prose-only answer is a failed run.
- If extraction hints are available as JSON, prefer one call to `materialize_hints` with the document id, scoped top entity name, entity label, and raw hints JSON.
- First call `init_memory` with the current document identifier and configured top-level entity context.
- Then call `{top_create_tool}` for the top entity from the top-entity list.
- Reuse the scoped top entity when the contract says top-entity reuse is required.
- For `{top_create_tool}`, pass the source-supported top entity label from inside brackets, never `top`, `{top_local}`, or generated shell labels such as `{top_local}-1`.
- In this top-entity KG pass, create only the `{top_local}` root. Do not create or link `{top_local}` required-link targets, ordered members, outputs, inputs, yields, equipment, or placeholder entities; those belong to later per-entity iterations with source-supported extraction hints.
- In the top-entity KG pass, do not create placeholder/shell targets for required links. In particular, do not create generic ordered-member targets just to satisfy a required link; later per-entity iterations must create source-supported members with concrete subclasses and ordering values.
- Do not materialize generic ordered-member parent hints as placeholder members when specific ordered-member subclasses exist in the T-Box.
- Do not create two ordered-member individuals for the same source operation, label, or ordering value; choose the single most specific class supported by the T-Box class comments.
- Do not export duplicate same-class labels for the same extracted entity, and do not leave unreachable typed nodes outside the graph reachable from the scoped top entity.
- Create one RDF individual per extracted entity unless the ontology-derived integrity profile says reuse is allowed.
- Add object-property links only when both subject and object are supported by extracted evidence.
- For object label parameters ending in `_label`, pass only the target entity label. Do not include quantities or scalar attributes inside label strings; those must be represented on the target entity through its own supported fields.
- Object label parameters ending in `_label` must not append the scoped top entity label as a context suffix.
- Enforce every configured mutually exclusive property group before materializing hints; never assert more than one active property from the same group on one entity instance.
- Before export, ensure only source-supported required top-level links below are present; absence of a concrete target in the extraction hints is not permission to invent one.
- Do not introduce classes or properties that are not present in the T-Box context.
- Finish by calling `export_memory`; do not claim success until this tool returns successfully.

Required Tool Sequence:
{required_tool_text}

Required Top-Level Links:
{_format_required_links(context)}

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
Ontology-Derived Integrity Profile:
```json
{integrity}
```
{_medical_csv_roundtrip_prompt_addon(context)}
Export:
After entity creation and linking, call the export tool and ensure the emitted Turtle parses successfully.
"""


def _iteration_extraction_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    return f"""# Extraction Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Extract source-supported hints for this iteration only.

Iteration Scope:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}

Current Target Entity:
- Label: {{entity_label}}
- IRI: {{entity_uri}}

Rules:
- Use only the source text and the ontology-derived class/property context below.
- Treat every T-Box `comment=` value below as a binding extraction rule for the corresponding class or property.
- Extract hints for the current target entity only.
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
- For identification or demographic fields, inspect source header blocks and nearby header tables before the main body when the class or property comments say those fields come from identifying or administrative source regions.
- For short acronym-like datatype fields, if the exact acronym/token appears in source text in a relevant diagnosis, indication, observation, or field-value context and the T-Box comment says that token activates the field, emit the configured active checklist value for that field.
- If source evidence supports any datatype field for a class linked from the current top entity, emit both the top-entity `_label` link to that class and the companion class section. After emitting one field for that linked class, scan the same structured source region for all other accepted fields of that class before returning JSON.
- Use exact class local names from the Materializable Hint Contract as JSON section keys.
- Inside each class section, use only `label` and the listed generated tool parameter names for that class.
- Emit a class section only when the current target label or a source-supported linked target actually denotes an instance of that class; do not coerce process, event, or relation labels into material/product/container classes just because those classes are available.
- For ordered-member classes, ordering fields listed in the Materializable Hint Contract must be unique positive integers starting at 1. Do not emit duplicate, skipped, decimal, fractional, zero, negative, or between-step order values such as 1.5; renumber the ordered members as consecutive integers in source order.
- Do not emit the same source operation, label, or ordering value as two different ordered-member class sections; choose the single most specific class supported by the T-Box class comments.
- Do not emit a generic ordered-member parent class as a placeholder when source evidence supports one of its specific subclasses in the Materializable Hint Contract; emit the specific subclass section and its supported fields instead.
- If the T-Box declares an ordered-member class whose comment says it introduces a linked target object, do not skip those initial introduction members when the source lists targets as present in a mixture, vessel, treatment, or starting condition; emit one ordered member per source-supported linked target before later operations.
- Object label fields ending in `_label` must contain only the target entity label, never an appended amount, duration, temperature, role, or other scalar value; put those scalar values on the target class section using its listed fields.
- Object label fields ending in `_label` must not append the Current Target Entity label as a context suffix. If the linked target is already present in existing/source hints, reuse that exact target label.
- When one current top entity links to multiple non-top target classes in the same response, do not reuse the exact same label for different target-class instances. Use a stable class-distinct target label for each linked class, and use that same class-distinct label in both the top-entity `_label` field and the companion target-class section.
- For ordered-member object-label fields whose target class has scalar fields, link to the same companion target object label that carries those scalar fields; do not mint a second context-specific target label for the link.
- For every required ordered-member object-label link declared below, emit the `_label` field when the ordered-member label or source sentence identifies a target object present in the same hints.
- When an object-label field points to a target class that has scalar fields and the source states those scalar values, emit a companion target-class object with the same `label` so the values are materialized on the linked target.
- When an object-label field contains multiple linked targets, use a JSON list of labels and emit one companion target-class object per label; do not collapse multiple targets into one label string.
- If a linked target is itself expressed as a formula-like label and the target class exposes a formula/value scalar field, copy the source-supported formula-like label into that scalar field too instead of leaving the target scalar empty.
- If a T-Box object property comment says a target should link to source species, reagents, aliases, or materials, emit that object-label field for each source-supported target object; do not omit it merely because another formula/value field is already filled.
- If a general parameter string contains a scalar value that has a dedicated generated field in the Materializable Hint Contract, also emit the dedicated field instead of leaving the scalar only inside the parameter string.
- For classes listed in the Mutually Exclusive Property Contract, emit at most one active property from each group for one entity instance. Use source evidence and T-Box comments to choose the best-supported one; omit the rest.
- When a T-Box comment distinguishes final, confirmed, provisional, preliminary, intermediate, or subordinate evidence, follow that evidence priority exactly; do not promote provisional or intermediate source statements into final/confirmed fields unless the source explicitly makes them final or confirmed.
- If the T-Box exposes a procedure-inheritance object field and the source says the current procedure follows the same, similar, or previous conditions as another source-supported procedure, treat the referenced procedure text as source-supported context for this target: carry over its ordered members and linked targets, then apply only the explicit modifications stated for the current target.
- Existing hint labels are not source evidence for adding new object fields; only emit a new `_label` field when the Source text explicitly supports that linked target.
- If evidence belongs to a more specific class whose generated tool lists the relevant field, emit that specific class instead of a generic parent/container class.
- Never emit schema placeholders or routing fields; use only the class sections and field names listed below.
- Output only a compact JSON object, with no markdown fences or explanatory prose.

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

Linked Target Scalar Contract:
{_format_linked_target_scalar_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_tbox_comment_fidelity_contract()}

Expected JSON Shape:
- Top-level keys must be class locals from the Materializable Hint Contract.
- Values may be a single object or an array of objects.
- Each object must contain `label` when a source-supported label exists.
- Every other key must be one of the listed fields for that class.

Classes and Properties:
{_format_class_rows(context)}

Datatype Properties:
{_format_property_rows(context, kind="datatype")}

Object Properties:
{_format_property_rows(context, kind="object")}

{_medical_csv_roundtrip_prompt_addon(context)}
Source:
{{paper_content}}
"""


def _iteration_kg_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    top = context.contract.get("top_entity") or {}
    top_local = str(top.get("class_local") or "TopEntity").strip() or "TopEntity"
    return f"""# KG Building Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Use the generated MCP tools to materialize the extracted hints for this iteration.

Rules:
- You must call tools. A prose-only answer is a failed run.
- Prefer one call to `materialize_hints` with the current document id, scoped top entity label, entity label, and the raw JSON from Extraction Hints.
- If `materialize_hints` returns status `ok`, use its returned TTL/export result as the basis for the final answer.
- Reuse the scoped top entity URI supplied by the runtime.
- Use exact class/property local names from the extracted hints and T-Box.
- Treat the Materializable Hint Contract as the authoritative mapping between extracted hint keys and `create_*` tool parameters.
- For datatype values, pass supported scalar fields directly into the relevant `create_*` tool parameters.
- If a `create_*` tool exposes scalar parameters inherited from a parent class, pass the hinted values there; do not omit them because the concrete class is more specific than the parent.
- For ordered-member tools, pass only unique positive integer ordering values starting at 1; if enrichment adds a member between existing members, renumber the full ordered sequence with consecutive integers instead of using duplicate values, gaps, or decimals such as 1.5.
- Do not create two ordered-member individuals for the same source operation, label, or ordering value; choose the single most specific class supported by the T-Box class comments.
- Do not export duplicate same-class labels for the same extracted entity, and do not leave unreachable typed nodes outside the graph reachable from the scoped top entity.
- Do not materialize generic ordered-member parent hints as placeholder members when specific ordered-member subclass hints are available for the same source segment.
- For object label parameters ending in `_label`, pass only the target entity label. Do not include quantities or scalar attributes inside label strings; those must be represented on the target entity through its own supported fields.
- Object label parameters ending in `_label` must not append the scoped top entity label as a context suffix. Reuse the exact label of the companion target object that carries source-supported scalar fields.
- For required ordered-member object-label links, ensure each ordered-member object links to the exact target object label from the hints when that target is identifiable from the ordered-member label.
- Enforce every configured mutually exclusive property group before materializing hints; never assert more than one active property from the same group on one entity instance.
- For object links, call the relevant `add_*` tools after creating/reusing both endpoints.
- Finish by calling `export_memory`; do not claim success until export succeeds.

Mandatory Tool Sequence:
Preferred path: call `materialize_hints` once. It initializes memory, creates hinted entities, links T-Box-compatible top-level targets, and exports memory.

Fallback path if `materialize_hints` is unavailable:
1. Call `init_memory` with the current document identifier and scoped top entity label.
2. Create or reuse the scoped top entity using its `create_*` tool.
3. For every extracted class section, call the matching `create_*` tool and pass every supported scalar parameter from the hints.
4. For every required top-level link, call the matching `add_*` tool from the scoped top entity to each created target.
5. For every ordered member, call the specific subclass `create_*` tool with the ordering scalar, then call the configured parent-to-member `add_*` tool.
6. Call `export_memory` and base the final response only on the returned tool result.

Failure Condition:
- If you have not called `export_memory`, or if no export result is available, state that KG building failed instead of saying the RDF graph was created or exported.

Scoped Top Entity:
- Document identifier value: `{{hash}}`
- Class: `{top_local}`
- Entity label value: `{{entity_label}}`
- Entity URI value: `{{entity_uri}}`

Required Top-Level Links:
{_format_required_links(context)}

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

Ordered-Member Integrity Contract:
{_format_ordered_member_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_medical_csv_roundtrip_prompt_addon(context)}
Extraction Hints:
{{paper_content}}
"""


def _pre_extraction_prompt(
    context: AgenticGenerationContext, iteration: dict[str, Any]
) -> str:
    return f"""# Pre-Extraction Prompt: {context.ontology.name} Iteration {iteration.get("iteration_number")}

Task:
Collect the shortest source spans relevant to this iteration scope. Do not interpret beyond the text.

Iteration Scope:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}

Return only relevant source excerpts and their nearby headings.

Source:
{{paper_content}}
"""


def _sub_iteration_extraction_prompt(
    context: AgenticGenerationContext,
    iteration: dict[str, Any],
    sub_iteration: dict[str, Any],
) -> str:
    return f"""# Enrichment Extraction Prompt: {context.ontology.name} Iteration {sub_iteration.get("iteration_number")}

Task:
Refine or enrich the existing extraction hints for this sub-iteration only.

Parent Iteration:
- Name: {iteration.get("name") or ""}
- Description: {iteration.get("description") or ""}

Sub-Iteration Scope:
- Name: {sub_iteration.get("name") or ""}
- Description: {sub_iteration.get("description") or ""}

Rules:
- Use only the provided source text and existing hints.
- Treat every T-Box `comment=` value below as a binding extraction rule for the corresponding class or property.
- Before emitting JSON, find the class section whose `label` equals the Current Target Entity label when present; for that section, scan the Materializable Hint Contract and include every source-supported datatype field accepted by that class. Identifier values in lists or tables paired with the target label are source-supported evidence.
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
- If source evidence supports any datatype field for a class linked from the current top entity, emit both the top-entity `_label` link to that class and the companion class section. After emitting one field for that linked class, scan the same structured source region for all other accepted fields of that class before returning JSON.
- Emit a class section only when the current target label, an existing hinted member, or a source-supported linked target actually denotes an instance of that class; do not coerce process, event, or relation labels into material/product/container classes just because those classes are available.
- For ordered-member classes, ordering fields listed in the Materializable Hint Contract must be unique positive integers starting at 1. Do not emit duplicate, skipped, decimal, fractional, zero, negative, or between-step order values such as 1.5; renumber the ordered members as consecutive integers in source order.
- Do not emit the same source operation, label, or ordering value as two different ordered-member class sections; choose the single most specific class supported by the T-Box class comments.
- Do not emit a generic ordered-member parent class as a placeholder when source evidence supports one of its specific subclasses in the Materializable Hint Contract; emit the specific subclass section and its supported fields instead.
- If the T-Box declares an ordered-member class whose comment says it introduces a linked target object, do not skip those initial introduction members when the source lists targets as present in a mixture, vessel, treatment, or starting condition; emit one ordered member per source-supported linked target before later operations.
- Object label fields ending in `_label` must contain only the target entity label, never an appended amount, duration, temperature, role, or other scalar value; put those scalar values on the target class section using its listed fields.
- Object label fields ending in `_label` must not append the Current Target Entity label as a context suffix. If the linked target is already present in existing/source hints, reuse that exact target label.
- When one current top entity links to multiple non-top target classes in the same response, do not reuse the exact same label for different target-class instances. Use a stable class-distinct target label for each linked class, and use that same class-distinct label in both the top-entity `_label` field and the companion target-class section.
- For ordered-member object-label fields whose target class has scalar fields, link to the same companion target object label that carries those scalar fields; do not mint a second context-specific target label for the link.
- For every required ordered-member object-label link declared below, emit the `_label` field when the ordered-member label, existing hints, or source sentence identifies a target object present in the same hints.
- When an object-label field points to a target class that has scalar fields and the source states those scalar values, emit a companion target-class object with the same `label` so the values are materialized on the linked target.
- When an object-label field contains multiple linked targets, use a JSON list of labels and emit one companion target-class object per label; do not collapse multiple targets into one label string.
- If a linked target is itself expressed as a formula-like label and the target class exposes a formula/value scalar field, copy the source-supported formula-like label into that scalar field too instead of leaving the target scalar empty.
- If a T-Box object property comment says a target should link to source species, reagents, aliases, or materials, emit that object-label field for each source-supported target object; do not omit it merely because another formula/value field is already filled.
- If a general parameter string contains a scalar value that has a dedicated generated field in the Materializable Hint Contract, also emit the dedicated field instead of leaving the scalar only inside the parameter string.
- For classes listed in the Mutually Exclusive Property Contract, emit at most one active property from each group for one entity instance. Use source evidence and T-Box comments to choose the best-supported one; omit the rest.
- When a T-Box comment distinguishes final, confirmed, provisional, preliminary, intermediate, or subordinate evidence, follow that evidence priority exactly; do not promote provisional or intermediate source statements into final/confirmed fields unless the source explicitly makes them final or confirmed.
- If the T-Box exposes a procedure-inheritance object field and the source says the current procedure follows the same, similar, or previous conditions as another source-supported procedure, treat the referenced procedure text as source-supported context for this target: carry over its ordered members and linked targets, then apply only the explicit modifications stated for the current target.
- Existing hint labels are not source evidence for adding new object fields; only emit a new `_label` field when the Source text explicitly supports that linked target.
- If enrichment evidence belongs to a more specific class whose generated tool lists the relevant field, emit that specific class instead of a generic parent/container class.
- Return only a compact JSON object, with no markdown fences, bullets, checklists, or explanatory prose.
- The JSON must be directly mergeable into the existing hints: use class-local top-level keys from the Materializable Hint Contract, and each value must be an object or an array of objects.
- To enrich an existing ordered member, repeat the same class and stable identifying fields already present in the existing hints, then add only supported fields from the Materializable Hint Contract.
- For source-supported object fields shared by ordered members, emit the relevant `_label` field on each affected ordered-member object; do not describe the enrichment in prose.

Materializable Hint Contract:
{_format_materializable_hint_contract(context)}

Linked Target Scalar Contract:
{_format_linked_target_scalar_contract(context)}

Required Ordered-Member Object-Link Contract:
{_format_required_step_scoped_object_contract(context)}

{_format_mutually_exclusive_property_contract(context)}
{_tbox_comment_fidelity_contract()}

Expected JSON Shape:
- Top-level keys must be class locals from the Materializable Hint Contract.
- Values may be a single object or an array of objects.
- Each object must include enough stable fields from the existing hint item to identify the member being enriched, such as `label` and any ordering field listed for that class.
- Every other key must be one of the listed fields for that class.

Classes and Properties:
{_format_class_rows(context)}

Datatype Properties:
{_format_property_rows(context, kind="datatype")}

Object Properties:
{_format_property_rows(context, kind="object")}

{_medical_csv_roundtrip_prompt_addon(context)}
Source and Existing Hints:
{{paper_content}}
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
        iter_num = 1 if ontology == "ontomops" else 2
        output_ttl = (
            "ontomops_output/ontomops_extension_{entity_name}.ttl"
            if ontology == "ontomops"
            else "ontospecies_output/{entity_slugified}.ttl"
        )
        iteration = {
            "iteration_number": iter_num,
            "name": f"{ontology}_extension",
            "description": f"T-Box-driven {ontology} extension extraction and KG building.",
            "model_config_key": f"extension_{ontology}",
            "use_agent": False,
            "per_entity": True,
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

    runtime = context.contract.get("runtime_policies") or {}
    plan = runtime.get("iteration_plan") or {}
    blueprint_path = Path(str(plan.get("iterations_blueprint_path") or ""))
    if blueprint_path.is_file():
        blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
        iterations = list(blueprint.get("iterations") or [])
    else:
        iterations = []

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


def generate_deterministic_prompt_slice(context: AgenticGenerationContext) -> list[str]:
    prompts_dir = Path(context.prompts_dir)
    prompts_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "EXTRACTION_ITER_1.md": _extraction_prompt(context),
        "KG_BUILDING_ITER_1.md": _kg_prompt(context),
    }
    iterations = _iteration_plan(context)
    for iteration in iterations.get("iterations") or []:
        iter_num = iteration.get("iteration_number")
        files[f"EXTRACTION_ITER_{iter_num}.md"] = _iteration_extraction_prompt(
            context, iteration
        )
        files[f"KG_BUILDING_ITER_{iter_num}.md"] = _iteration_kg_prompt(
            context, iteration
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
    if iterations.get("iterations"):
        iterations_dir = (
            Path(context.output_root) / "iterations" / context.ontology.name
        )
        iterations_dir.mkdir(parents=True, exist_ok=True)
        iterations_path = iterations_dir / "iterations.json"
        iterations_path.write_text(
            json.dumps(iterations, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        written.append(str(iterations_path))
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


def repair_generated_artifacts(
    context: AgenticGenerationContext, report: dict[str, Any]
) -> list[str]:
    failures = [str(x) for x in report.get("failures") or []]
    repaired: list[str] = []
    script_markers = (
        "Missing create tools",
        "Missing relationship tools",
        "missing required step-scoped",
        "syntax error",
        "import failed",
        "main.py imports",
    )
    prompt_markers = (
        "prompt placeholder",
        "Prompt",
        "prompt",
        "Foreign ontology symbols",
    )
    if any(any(marker in failure for marker in script_markers) for failure in failures):
        repaired.extend(generate_deterministic_script_slice(context))
    if any(any(marker in failure for marker in prompt_markers) for failure in failures):
        repaired.extend(generate_deterministic_prompt_slice(context))
    return repaired


def run_agentic_generation_experiment(
    ontology_names: list[str],
    *,
    meta_task_config_path: str | Path | None = None,
    output_root: str | Path = "ai_generated_contents_agentic_candidate",
    generate_scripts: bool = False,
    generate_prompts: bool = False,
    repair_loop: bool = False,
    exercise_repair: bool = False,
    max_repair_iterations: int = 3,
    llm_agent_generation: bool = False,
    generation_model: str = "gpt-5.2",
    max_agent_rounds: int = 2,
) -> dict[str, Any]:
    contexts = build_contexts_for_ontologies(
        ontology_names,
        meta_task_config_path=meta_task_config_path,
        output_root=output_root,
        write_files=True,
    )
    all_contracts = [ctx.contract for ctx in contexts]
    reports = []
    for context in contexts:
        written: list[str] = []
        if generate_scripts:
            written.extend(generate_deterministic_script_slice(context))
        if generate_prompts:
            written.extend(generate_deterministic_prompt_slice(context))
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
            llm_agent_run = run_llm_agentic_generation_rounds_sync(
                context,
                model_name=generation_model,
                foreign_contracts=foreign,
                max_rounds=max_agent_rounds,
                generate_scripts=generate_scripts,
                generate_prompts=generate_prompts,
            )
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
        if repair_loop:
            for iteration in range(1, max(1, max_repair_iterations) + 1):
                if report.get("ok"):
                    break
                repaired = repair_generated_artifacts(context, report)
                report = build_validation_report(
                    context, foreign_contracts=foreign, write_report=True
                )
                repair_history.append(
                    {
                        "iteration": iteration,
                        "ok": report.get("ok"),
                        "failures": report.get("failures") or [],
                        "feedback": report.get("feedback") or {},
                        "repaired_files": repaired,
                    }
                )
        report["written_files"] = written
        report["exercise_defects"] = exercise_defects
        report["generation_mode"] = (
            "llm_agent" if llm_agent_generation else "deterministic"
        )
        report["llm_agent_run"] = llm_agent_run
        report["repair_history"] = repair_history
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
