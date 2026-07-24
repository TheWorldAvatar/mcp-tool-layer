from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, RDFS

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
)
from src.agents.scripts_and_prompts_generation.generation_contracts import (
    validate_generated_artifacts,
)


EXPECTED_SCRIPT_SUFFIXES = (
    "_creation_base.py",
    "_creation_checks.py",
    "_creation_entities.py",
    "_creation_relationships.py",
    "main.py",
)

MEDICAL_CSV_ROUNDTRIP_PROMPT_HEADER = "## CSV Round-Trip Contract"


def medical_csv_roundtrip_contract_addon() -> str:
    """
    Text appended to medical extraction / KG prompts so JSON hints and tool calls stay
    compatible with fixed-column CSV round-trips without embedding benchmark-specific
    extraction facts. Domain-specific evidence rules should live in the T-Box comments.
    """
    return (
        f"{MEDICAL_CSV_ROUNDTRIP_PROMPT_HEADER}\n"
        "- For spreadsheet checklist fields, use JSON string values such as `\"1\"` for checked/active "
        "and `\"-\"` (or omit the field) for inactive, unless the T-Box comment specifies another literal convention. "
        "Never emit JSON booleans `true`/`false` or the strings `True`/`False` for checklist scalars.\n"
        "- Preserve datatype values in the normalized form required by the relevant T-Box comment; do not add "
        "case-specific normalization rules that are absent from the T-Box.\n"
        "- When one top entity links to multiple non-top target classes, never reuse the exact same `label` for "
        "different target-class instances. Use stable class-distinct labels and put those exact labels in the "
        "matching object-label fields.\n"
        "- Treat the Materializable Hint Contract and T-Box comments as the authoritative source for field names, "
        "allowed values, conditional gates, positive evidence, and negative evidence.\n"
    )


def _medical_csv_roundtrip_prompt_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if context.ontology.name != "medical":
        return failures, warnings
    prompts_dir = Path(context.prompts_dir)
    if not prompts_dir.is_dir():
        warnings.append(
            "Medical CSV round-trip validation skipped because prompts_dir is missing"
        )
        return failures, warnings
    required_phrases = (
        MEDICAL_CSV_ROUNDTRIP_PROMPT_HEADER,
        "spreadsheet checklist fields",
        "Never emit JSON booleans",
        "class-distinct labels",
        "T-Box comments",
    )
    for path in sorted(prompts_dir.glob("*.md")):
        name = path.name
        if name.startswith("PRE_EXTRACTION_"):
            continue
        if name == "EXTRACTION_ITER_1.md":
            continue
        if not (
            name.startswith("EXTRACTION_ITER_") or name.startswith("KG_BUILDING_ITER_")
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for phrase in required_phrases:
            if phrase not in text:
                short = phrase if len(phrase) <= 72 else phrase[:69] + "..."
                failures.append(
                    f"{name}: medical prompt missing CSV round-trip marker `{short}`"
                )
    return failures, warnings


def _local_name(iri: Any) -> str:
    text = str(iri or "").strip()
    return text.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1] if text else ""


def _normalized_symbol(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _predicate_target_stem(predicate_local: str) -> str:
    text = str(predicate_local or "").strip()
    for prefix in ("has", "is"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :]
    return text


def _read_texts(root: Path, pattern: str) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        p.name: p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(root.glob(pattern))
    }


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


def _syntax_report(scripts_dir: Path) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if not scripts_dir.exists():
        warnings.append(f"Scripts directory does not exist yet: {scripts_dir}")
        return failures, warnings
    for path in sorted(scripts_dir.glob("*.py")):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            failures.append(f"{path.name}: syntax error line {exc.lineno}: {exc.msg}")
    return failures, warnings


def _import_report(context: AgenticGenerationContext) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    scripts_dir = Path(context.scripts_dir)
    main_path = scripts_dir / "main.py"
    if not main_path.exists():
        warnings.append("main.py not present; import smoke skipped")
        return failures, warnings
    package_name = f"_agentic_generated_{context.ontology.name}_{abs(hash(str(scripts_dir.resolve())))}"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts_dir.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    module_name = f"{package_name}.main"
    try:
        spec = importlib.util.spec_from_file_location(module_name, main_path)
        if spec is None or spec.loader is None:
            failures.append("main.py import failed: could not create import spec")
            return failures, warnings
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        if getattr(module, "mcp", None) is None:
            failures.append("main.py imports but does not expose `mcp`")
    except Exception as exc:
        failures.append(f"main.py import failed: {type(exc).__name__}: {exc}")
    return failures, warnings


def _import_generated_main_module(scripts_dir: Path, ontology_name: str):
    package_name = f"_agentic_generated_runtime_{ontology_name}_{abs(hash(str(scripts_dir.resolve())))}"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts_dir.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    module_name = f"{package_name}.main"
    spec = importlib.util.spec_from_file_location(module_name, scripts_dir / "main.py")
    if spec is None or spec.loader is None:
        raise AssertionError("Could not create import spec for generated main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _first_ordered_leaf_class(context: AgenticGenerationContext) -> str:
    classes = context.parsed.get("classes") or {}
    ordered = [
        str(x).strip()
        for x in (
            (context.contract.get("ordered_member_profile") or {}).get(
                "ordered_member_classes"
            )
            or []
        )
        if str(x).strip()
    ]
    ordered_set = set(ordered)
    parent_classes = {
        parent
        for cls in ordered
        for parent in ((classes.get(cls) or {}).get("parent_classes") or [])
        if parent in ordered_set
    }
    for cls in ordered:
        if cls not in parent_classes:
            return cls
    return ordered[0] if ordered else ""


def _build_runtime_probe_hints(context: AgenticGenerationContext) -> dict[str, Any]:
    classes = context.parsed.get("classes") or {}
    top_local = str(
        (context.contract.get("top_entity") or {}).get("class_local") or ""
    ).strip()
    hints: dict[str, Any] = {}
    known_classes = set(classes)
    top_cls = classes.get(top_local) or {}
    ordered_classes = {
        str(x).strip()
        for x in (
            (context.contract.get("ordered_member_profile") or {}).get(
                "ordered_member_classes"
            )
            or []
        )
        if str(x).strip()
    }

    for prop, range_local in sorted((top_cls.get("object_properties") or {}).items()):
        prop = str(prop or "").strip()
        range_local = str(range_local or "").strip()
        predicate_stem = _normalized_symbol(_predicate_target_stem(prop))
        for class_local, class_spec in sorted(classes.items()):
            if class_local == top_local:
                continue
            if class_local in ordered_classes:
                continue
            ancestors = set((class_spec or {}).get("parent_classes") or [])
            if (
                class_local == range_local
                or range_local in ancestors
                or _normalized_symbol(class_local) == predicate_stem
            ):
                hints.setdefault(class_local, {"label": f"Validator {class_local}"})

    for spec in context.contract.get("required_links") or []:
        range_local = _local_name((spec or {}).get("target_class_iri"))
        if range_local and range_local in known_classes and range_local != top_local:
            hints.setdefault(range_local, {"label": f"Validator {range_local}"})

    required_step_specs = (
        context.contract.get("required_step_scoped_object_properties") or []
    )
    for spec in required_step_specs:
        range_local = str((spec or {}).get("range_local") or "").strip()
        if range_local and range_local in known_classes:
            hints.setdefault(range_local, {"label": f"Validator {range_local}"})

    ordered_class = _first_ordered_leaf_class(context)
    if ordered_class and ordered_class in known_classes:
        order_props = [
            str(x).strip()
            for x in (
                (context.contract.get("ordered_member_profile") or {}).get(
                    "single_valued_ordering_properties"
                )
                or []
            )
            if str(x).strip()
        ]
        payload: dict[str, Any] = {
            "label": f"Validator {ordered_class}",
            (order_props[0] if order_props else "hasOrder"): 1,
        }
        for spec in required_step_specs:
            if str((spec or {}).get("domain_local") or "").strip() == ordered_class:
                predicate = str((spec or {}).get("predicate_local") or "").strip()
                range_local = str((spec or {}).get("range_local") or "").strip()
                if predicate and range_local:
                    payload[f"{predicate}_label"] = f"Validator {range_local}"
        hints[ordered_class] = [payload, dict(payload)]

    if not hints and top_local:
        hints[top_local] = {"label": "Validator Top"}
    if not hints:
        for class_local in sorted(known_classes):
            if class_local:
                hints[class_local] = {"label": f"Validator {class_local}"}
                break
    return hints


def _runtime_graph_hygiene_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    scripts_dir = Path(context.scripts_dir)
    main_path = scripts_dir / "main.py"
    if not main_path.exists():
        warnings.append(
            "Runtime graph hygiene validation skipped because main.py is missing"
        )
        return failures, warnings

    try:
        module = _import_generated_main_module(scripts_dir, context.ontology.name)
    except Exception as exc:
        warnings.append(
            f"Runtime graph hygiene validation skipped because generated main.py could not be imported: {type(exc).__name__}: {exc}"
        )
        return failures, warnings

    materialize = getattr(module, "materialize_hints", None)
    if not callable(materialize) and callable(getattr(materialize, "fn", None)):
        materialize = materialize.fn
    if not callable(materialize):
        warnings.append(
            "Runtime graph hygiene validation skipped because materialize_hints is missing"
        )
        return failures, warnings

    hints = _build_runtime_probe_hints(context)
    previous_data_dir = os.environ.get("TWA_AGENTIC_DATA_DIR")
    try:
        with tempfile.TemporaryDirectory(prefix="agentic_runtime_hygiene_") as tmp_dir:
            os.environ["TWA_AGENTIC_DATA_DIR"] = tmp_dir
            raw_result = materialize(
                "validator-doi",
                "validator-top",
                "Validator Top",
                json.dumps(hints, ensure_ascii=False),
            )
            try:
                result = json.loads(str(raw_result or "{}"))
            except json.JSONDecodeError as exc:
                failures.append(
                    f"Generated materialize_hints must return JSON during runtime graph hygiene validation: {exc}"
                )
                return failures, warnings
            if result.get("status") != "ok":
                failures.append(
                    "Generated materialize_hints failed runtime graph hygiene validation: "
                    + str(result.get("message") or result)
                )
                return failures, warnings

            ttl = str(result.get("ttl") or "")
            if not ttl.strip():
                failures.append(
                    "Generated materialize_hints returned no TTL for runtime graph hygiene validation"
                )
                return failures, warnings
            graph = Graph()
            graph.parse(data=ttl, format="turtle")
    except Exception as exc:
        failures.append(
            f"Generated runtime graph hygiene validation failed: {type(exc).__name__}: {exc}"
        )
        return failures, warnings
    finally:
        if previous_data_dir is None:
            os.environ.pop("TWA_AGENTIC_DATA_DIR", None)
        else:
            os.environ["TWA_AGENTIC_DATA_DIR"] = previous_data_dir

    class_iris = {
        str((spec or {}).get("iri") or "").strip(): local
        for local, spec in (context.parsed.get("classes") or {}).items()
        if str((spec or {}).get("iri") or "").strip()
    }
    typed_nodes: dict[URIRef, set[str]] = {}
    label_groups: dict[tuple[str, str], set[URIRef]] = {}
    for subject, _, class_iri in graph.triples((None, RDF.type, None)):
        if not isinstance(subject, URIRef):
            continue
        class_local = class_iris.get(str(class_iri))
        if not class_local:
            continue
        typed_nodes.setdefault(subject, set()).add(class_local)
        for label in graph.objects(subject, RDFS.label):
            label_text = str(label or "").strip()
            if label_text:
                label_groups.setdefault((class_local, label_text), set()).add(subject)

    duplicate_labels = [
        f"{class_local}:{label}"
        for (class_local, label), nodes in sorted(label_groups.items())
        if len(nodes) > 1
    ]
    if duplicate_labels:
        failures.append(
            "Generated runtime graph contains duplicate same-class labels after materialize_hints: "
            + ", ".join(duplicate_labels[:8])
        )

    top_iri = str(
        (json.loads(raw_result).get("top_iri") if raw_result else "") or ""
    ).strip()
    reachable: set[URIRef] = set()
    if top_iri:
        frontier = [URIRef(top_iri)]
        while frontier:
            current = frontier.pop()
            if current in reachable:
                continue
            reachable.add(current)
            for _, predicate, obj in graph.triples((current, None, None)):
                if predicate in {RDF.type, RDFS.label}:
                    continue
                if isinstance(obj, URIRef) and obj not in reachable:
                    frontier.append(obj)
    else:
        warnings.append(
            "Runtime graph hygiene validation could not inspect reachability because top_iri is missing"
        )

    if reachable:
        unreachable = [
            f"{sorted(classes)[0]}:{node}"
            for node, classes in sorted(
                typed_nodes.items(), key=lambda item: str(item[0])
            )
            if node not in reachable
        ]
        if unreachable:
            failures.append(
                "Generated runtime graph contains typed nodes unreachable from the materialized top entity: "
                + ", ".join(unreachable[:8])
            )
    return failures, warnings


def _expected_tool_surface_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    scripts_dir = Path(context.scripts_dir)
    script_text = "\n".join(_read_texts(scripts_dir, "*.py").values())
    if not script_text.strip():
        warnings.append("No generated scripts found for tool-surface validation")
        return failures, warnings
    forbidden_extension_imports = (
        "src.ontomops_extension",
        "src.ontospecies_extension",
    )
    if context.ontology.role == "extension":
        used = [name for name in forbidden_extension_imports if name in script_text]
        if used:
            failures.append(
                "Generated extension MCP scripts must be T-Box-derived and must not wrap handwritten extension servers: "
                + ", ".join(used)
            )

    for tool_name in ("init_memory", "export_memory", "materialize_hints"):
        if (
            f'name="{tool_name}"' not in script_text
            and f"name='{tool_name}'" not in script_text
        ):
            failures.append(f"Missing required MCP tool: {tool_name}")
    if "materialize_hints" in script_text and not (
        "_canonical_hint_class" in script_text
        and "rsplit" in script_text
        and "isdigit" in script_text
        and 'split("#", 1)' in script_text
    ):
        failures.append(
            "Generated materializer must canonicalize repeat-indexed hint section keys "
            "back to known T-Box class locals before rejecting unsupported classes"
        )
    if (
        "materialize_hints" in script_text
        and "create_"
        not in script_text.split("def _canonical_hint_class", 1)[-1].split("def ", 1)[0]
    ):
        failures.append(
            "Generated materializer must canonicalize accidental tool-name hint keys back to T-Box class locals"
        )
    if "materialize_hints" in script_text and not (
        "_class_matches_top_link" in script_text
        and "accepted_classes" in script_text
        and "_created_nodes_unreachable" in script_text
    ):
        failures.append(
            "Generated materializer must link every created hinted node into the top graph before export, "
            "including predicate-stem top-link classes whose T-Box range is an external quantity class"
        )
    top_local = str(
        (context.contract.get("top_entity") or {}).get("class_local") or ""
    ).strip()
    if top_local and f"prefer_top=True" in script_text and not (
        "_is_generic_top_entity_label" in script_text
        and "generic top entity label" in script_text
        and top_local in script_text
    ):
        failures.append(
            "Generated top-entity creation must reject runtime/shell labels such as "
            f"`top` and `{top_local}-1`; the source-supported entity label must be passed instead"
        )
    if (
        top_local
        and "_ensure_required_top_links_before_export" in script_text
        and (
            '_create_entity("SynthesisStep", "Step 1")' in script_text
            or "placeholder if missing" in script_text
            or "_ensure_at_least_one_step_for_synthesis" in script_text
            or "_ensure_chemicaloutput_link_for_synthesis" in script_text
        )
    ):
        failures.append(
            "Generated export hooks must not invent placeholder required-link targets; "
            "missing outputs, ordered members, and yields must be created only from source-supported hints"
        )
    namespace = str(context.contract.get("namespace_uri") or "")
    external_predicates = [
        str((prop or {}).get("iri") or "")
        for prop in (context.parsed.get("properties") or {}).values()
        if str((prop or {}).get("iri") or "").strip()
        and namespace
        and not str((prop or {}).get("iri") or "").startswith(namespace)
    ]
    if external_predicates and "PREDICATE_URIS" not in script_text:
        failures.append(
            "Generated scripts must preserve full predicate IRIs for ontology-declared properties outside the local namespace"
        )

    classes = sorted((context.parsed.get("classes") or {}).keys())
    object_props = {
        name: prop
        for name, prop in (context.parsed.get("properties") or {}).items()
        if (prop or {}).get("kind") == "object"
    }
    missing_create = [cls for cls in classes if f"def create_{cls}" not in script_text]
    missing_links = [
        prop for prop in object_props if f"def add_{prop}" not in script_text
    ]
    if missing_create:
        failures.append(
            "Missing create tools for classes: " + ", ".join(missing_create[:20])
        )
    if missing_links:
        failures.append(
            "Missing relationship tools for object properties: "
            + ", ".join(missing_links[:20])
        )

    has_object_label_params = (
        re.search(r"\b[A-Za-z0-9_]+_label\s*=", script_text) is not None
    )
    has_label_reuse_normalization = bool(
        re.search(r"re\.sub\([^)]*\[[^\]]*0-9", script_text, flags=re.DOTALL)
        and re.search(r"\bcandidates?\s*=", script_text)
    )
    if has_object_label_params and not has_label_reuse_normalization:
        failures.append(
            "Generated object-label lookup must retry reuse with a normalized target label "
            "before minting a new linked individual"
        )
    if has_object_label_params and not (
        "CURRENT_ENTITY_CONTEXT" in script_text and "context_suffix" in script_text
    ):
        failures.append(
            "Generated object-label lookup must retry reuse after stripping an appended scoped top-entity context suffix"
        )
    if has_object_label_params and not (
        (
            "target_labels =" in script_text
            and "isinstance(" in script_text
            and "list" in script_text
        )
        or "_as_iterable(" in script_text
        or "_as_label_list(" in script_text
    ):
        failures.append(
            "Generated object-label link creation must handle list-valued target labels as separate linked individuals"
        )

    quantity_ranges = {
        str((spec or {}).get("range_local") or "").strip()
        for spec in context.contract.get("step_scoped_object_properties") or []
        if str((spec or {}).get("range_local") or "").strip()
    }
    quantity_ranges.update(
        str(
            ((context.parsed.get("properties") or {}).get(prop_local) or {}).get(
                "range"
            )
            or ""
        ).strip()
        for prop_local in (context.contract.get("relationship_domain_contracts") or {})
    )
    has_numeric_object_label_targets = any(
        range_local
        and re.search(rf"\bNS\.{re.escape(range_local)}\b", script_text)
        and re.search(
            rf"{re.escape(range_local)}[^,\n]*,\s*[A-Za-z0-9_]+_label", script_text
        )
        for range_local in quantity_ranges
    )
    has_numeric_label_materialization = (
        "ontology-of-units-of-measure.org" in script_text
        and "hasNumericalValue" in script_text
        and "hasUnit" in script_text
    )
    if has_numeric_object_label_targets and not has_numeric_label_materialization:
        failures.append(
            "Generated object-label target creation must preserve numeric label values "
            "as machine-readable quantity metadata when the label begins with a number"
        )
    if _mutually_exclusive_property_groups(context) and not (
        "_MUTUALLY_EXCLUSIVE_PROPERTY_GROUPS" in script_text
        and "_apply_mutually_exclusive_property_groups" in script_text
        and "Mutually exclusive property group" in script_text
    ):
        failures.append(
            "Generated materializer must enforce configured mutually exclusive datatype-property groups"
        )
    amount_like_target_ranges = {
        str(range_local or "").strip()
        for cls in (context.parsed.get("classes") or {}).values()
        for range_local in ((cls or {}).get("object_properties") or {}).values()
        if str(range_local or "").strip()
        and any(
            "amount" in str(prop_local).lower()
            for prop_local in (
                (
                    (context.parsed.get("classes") or {}).get(str(range_local).strip())
                    or {}
                )
                .get("datatype_properties", {})
                .keys()
            )
        )
    }
    amount_like_target_ranges.update(
        str((spec or {}).get("range_local") or "").strip()
        for spec in context.contract.get("step_scoped_object_properties") or []
        if str((spec or {}).get("range_local") or "").strip()
        and any(
            "amount" in str(prop_local).lower()
            for prop_local in (
                (
                    (context.parsed.get("classes") or {}).get(
                        str((spec or {}).get("range_local") or "").strip()
                    )
                    or {}
                )
                .get("datatype_properties", {})
                .keys()
            )
        )
    )
    if (
        has_object_label_params
        and amount_like_target_ranges
        and not (
            "_split_label_scalar" in script_text and "embedded_scalar" in script_text
        )
    ):
        failures.append(
            "Generated object-label target creation must strip trailing numeric parentheticals "
            "and persist them into amount-like target datatype fields when declared by the T-Box"
        )
    return failures, warnings


def _ordered_member_contract_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    scripts_dir = Path(context.scripts_dir)
    prompts_dir = Path(context.prompts_dir)
    entities_text = "\n".join(
        _read_texts(scripts_dir, "*_creation_entities.py").values()
    )
    prompt_text = "\n".join(_read_texts(prompts_dir, "KG_BUILDING_ITER*.md").values())
    if not entities_text.strip():
        warnings.append(
            "No generated entity script found for ordered-member contract validation"
        )
        return failures, warnings
    base_text = "\n".join(_read_texts(scripts_dir, "*_creation_base.py").values())

    profile = context.contract.get("ordered_member_profile") or {}
    ordered_classes = {
        str(x).strip()
        for x in profile.get("ordered_member_classes", []) or []
        if str(x).strip()
    }
    ordering_props = [
        str(x).strip()
        for x in profile.get("single_valued_ordering_properties", []) or []
        if str(x).strip()
    ]
    if not ordered_classes or not ordering_props:
        return failures, warnings
    if "ORDERING_PROPERTY_LOCALS" not in base_text:
        failures.append(
            "Generated base script must declare T-Box ordering properties for integer enforcement"
        )
    if "_coerce_positive_integer_order" not in base_text:
        failures.append(
            "Generated base script must reject non-positive or fractional ordered-member values"
        )
    if "datatype=XSD.integer" not in base_text:
        failures.append(
            "Generated base script must serialize ordered-member values as xsd:integer literals"
        )
    if "order < 1" not in base_text:
        failures.append(
            "Generated base script must reject zero and negative ordered-member values"
        )
    for prop_local in ordering_props:
        if prop_local not in base_text:
            failures.append(
                f"Generated base script ordering enforcement missing T-Box property `{prop_local}`"
            )
    main_text = "\n".join(_read_texts(scripts_dir, "main.py").values())
    if "_ORDERED_PARENT_CLASSES_WITH_SUBCLASSES" not in main_text:
        failures.append(
            "Generated main script must reject generic ordered-member parent placeholders when specific subclasses exist"
        )
    if "canonical_class in _ORDERED_PARENT_CLASSES_WITH_SUBCLASSES" not in main_text:
        failures.append(
            "Generated materializer must skip generic ordered-member parent placeholder hints"
        )
    if (
        "_extract_order_value" not in main_text
        or "unique positive integers" not in main_text
    ):
        failures.append(
            "Generated materializer must reject missing, duplicate, or invalid ordered-member values"
        )
    if (
        "_prepare_materialization_items" not in main_text
        or "_ordered_member_signature" not in main_text
    ):
        failures.append(
            "Generated materializer must deterministically collapse duplicate same-operation ordered-member hints before export"
        )
    if (
        context.contract.get("required_step_scoped_object_properties")
        and "_synthesize_missing_required_ordered_members" not in main_text
    ):
        failures.append(
            "Generated materializer must synthesize missing required ordered-member links from scoped target objects when no such members were extracted"
        )
    if context.contract.get("required_step_scoped_object_properties"):
        if "_augment_required_step_scoped_labels" not in main_text:
            failures.append(
                "Generated materializer must infer missing required ordered-member object labels from same-hint target labels"
            )
        if "_index_hint_labels_by_class" not in main_text:
            failures.append(
                "Generated materializer must index same-hint target labels for required ordered-member object links"
            )
        if "_existing_graph_labels_for_class" not in main_text:
            failures.append(
                "Generated materializer must also inspect already-loaded graph labels for required ordered-member object links"
            )

    classes = context.parsed.get("classes") or {}
    for class_local in sorted(ordered_classes & set(classes)):
        fn_match = re.search(
            rf"def\s+create_{re.escape(class_local)}\((?P<params>[^)]*)\)\s*->\s*str:\n(?P<body>.*?)(?=\ndef\s+create_|\Z)",
            entities_text,
            flags=re.DOTALL,
        )
        if not fn_match:
            failures.append(
                f"Ordered-member class `{class_local}` has no generated create tool"
            )
            continue
        params = fn_match.group("params")
        body = fn_match.group("body")
        for prop_local in ordering_props:
            if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(prop_local)}\s*=", params):
                failures.append(
                    f"Ordered-member create tool `create_{class_local}` missing T-Box ordering scalar parameter `{prop_local}`"
                )
            if not re.search(
                rf"_add_literal\(str\(iri\),\s*['\"]{re.escape(prop_local)}['\"],\s*{re.escape(prop_local)}\)",
                body,
            ):
                failures.append(
                    f"Ordered-member create tool `create_{class_local}` does not persist ordering scalar `{prop_local}`"
                )
        for parent in (classes.get(class_local) or {}).get("parent_classes", []) or []:
            parent_type_patterns = (
                rf"GRAPH\.add\(\(iri,\s*RDF\.type,\s*NS\[['\"]{re.escape(parent)}['\"]\]\)\)",
                rf"GRAPH\.add\(\(iri,\s*RDF\.type,\s*NS\.{re.escape(parent)}\)\)",
            )
            if parent in classes and not any(
                re.search(pattern, body) for pattern in parent_type_patterns
            ):
                failures.append(
                    f"Ordered-member create tool `create_{class_local}` does not preserve parent type `{parent}`"
                )

    required_prompt_markers = [
        "Ordered-Member Integrity Contract:",
        "Mandatory Tool Sequence:",
        "export_memory",
    ]
    if prompt_text.strip():
        for marker in required_prompt_markers:
            if marker not in prompt_text:
                failures.append(
                    f"KG prompt missing ordered-member enforcement marker `{marker}`"
                )
        if "positive integers starting at 1" not in prompt_text:
            failures.append(
                "KG prompt missing positive-integer ordered-member enforcement"
            )
        if "decimals such as 1.5" not in prompt_text:
            failures.append(
                "KG prompt missing explicit ban on decimal ordered-member insertion values"
            )
        if "duplicate" not in prompt_text or "gaps" not in prompt_text:
            failures.append(
                "KG prompt missing duplicate/gap ban for ordered-member values"
            )
        if "generic ordered-member parent" not in prompt_text:
            failures.append(
                "KG prompt missing ban on generic ordered-member parent placeholders"
            )
    else:
        warnings.append(
            "No KG-building prompts found for ordered-member prompt validation"
        )
    return failures, warnings


def _ttl_export_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    ttl_files = sorted(
        Path(context.output_root).glob(f"**/{context.ontology.name}*.ttl")
    )
    if not ttl_files:
        warnings.append("No generated TTL exports found; TTL parse validation skipped")
        return failures, warnings
    for ttl_file in ttl_files:
        try:
            Graph().parse(ttl_file, format="turtle")
        except Exception as exc:
            failures.append(f"{ttl_file}: Turtle parse failed: {exc}")
    return failures, warnings


def _prompt_quality_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    prompts_dir = Path(context.prompts_dir)
    prompt_texts = _read_texts(prompts_dir, "*.md")
    if not prompt_texts:
        warnings.append("No prompt files found; prompt quality validation skipped")
        return failures, warnings

    def _py_name(name: str) -> str:
        out = re.sub(r"\W+", "_", str(name or "")).strip("_")
        if not out:
            return "unnamed"
        return f"_{out}" if out[:1].isdigit() else out

    def _prompt_field_allowlist() -> dict[str, set[str]]:
        raw = context.contract.get("prompt_field_allowlist")
        if not isinstance(raw, dict):
            return {}
        return {
            str(class_local): {str(field) for field in fields if str(field)}
            for class_local, fields in raw.items()
            if isinstance(fields, list)
        }

    def _class_comment_expected(class_local: str) -> bool:
        allowlist = _prompt_field_allowlist()
        if class_local not in allowlist:
            return True
        return bool(allowlist[class_local])

    def _property_comment_expected(prop_local: str, spec: dict[str, Any]) -> bool:
        allowlist = _prompt_field_allowlist()
        if not allowlist:
            return True
        kind = str((spec or {}).get("kind") or "")
        domains = [str(x) for x in ((spec or {}).get("domains") or []) if str(x)]
        field = _py_name(prop_local)
        if kind == "object":
            field = f"{field}_label"
        return any(domain not in allowlist or field in allowlist[domain] for domain in domains)

    def _missing_comment_locals(text: str) -> list[str]:
        missing: list[str] = []
        for local, spec in sorted((context.parsed.get("classes") or {}).items()):
            if not _class_comment_expected(local):
                continue
            comment = str((spec or {}).get("comment") or "").strip()
            if comment and comment[:80] not in text:
                missing.append(local)
        for local, spec in sorted((context.parsed.get("properties") or {}).items()):
            if not _property_comment_expected(local, spec or {}):
                continue
            comment = str((spec or {}).get("comment") or "").strip()
            if comment and comment[:80] not in text:
                missing.append(local)
        return missing

    # The literal word "placeholder" can be legitimate ontology text (for
    # example a T-Box policy about placeholder targets). Flag actionable
    # template residue instead of the word in isolation.
    placeholder_re = re.compile(r"TODO|FIXME|<[^>\n]+>|\{\{[^}\n]+\}\}", re.IGNORECASE)
    for name, text in prompt_texts.items():
        matches = sorted(set(m.group(0) for m in placeholder_re.finditer(text)))
        if matches:
            failures.append(
                f"{name}: unresolved prompt placeholder/residue: {', '.join(matches[:8])}"
            )
        if name.startswith("EXTRACTION_ITER_") and name != "EXTRACTION_ITER_1.md":
            if "Materializable Hint Contract:" not in text:
                failures.append(
                    f"{name}: extraction prompt missing materializable hint/tool-parameter contract"
                )
            if (
                "T-Box Comment Fidelity Contract:" not in text
                or "binding extraction rule" not in text
            ):
                failures.append(
                    f"{name}: extraction prompt missing generic T-Box comment fidelity contract"
                )
            if "prevention, avoidance" not in text or "negative evidence" not in text:
                failures.append(
                    f"{name}: extraction prompt missing prevention/negation evidence guard"
                )
            if "Datatype Properties:" not in text or "Object Properties:" not in text:
                failures.append(
                    f"{name}: extraction prompt missing T-Box property comment sections"
                )
            missing_comments = _missing_comment_locals(text)
            if missing_comments:
                failures.append(
                    f"{name}: extraction prompt omits T-Box comments for {', '.join(missing_comments[:8])}"
                    + (" ..." if len(missing_comments) > 8 else "")
                )
            if (
                "Linked Target Scalar Contract:" not in text
                or "companion target-class object" not in text
            ):
                failures.append(
                    f"{name}: extraction prompt missing linked-target scalar companion contract"
                )
            if (
                "class section whose `label` equals the Current Target Entity label"
                not in text
            ):
                failures.append(
                    f"{name}: extraction prompt missing current-target datatype scan contract"
                )
            if "_label` must contain only the target entity label" not in text:
                failures.append(
                    f"{name}: extraction prompt missing pure `_label` field contract"
                )
            if (
                "must not append the Current Target Entity label as a context suffix"
                not in text
            ):
                failures.append(
                    f"{name}: extraction prompt missing ban on context-suffixed object-label fields"
                )
            if "generic ordered-member parent class as a placeholder" not in text:
                failures.append(
                    f"{name}: extraction prompt missing ban on generic ordered-member parent placeholders"
                )
            if "same companion target object label" not in text:
                failures.append(
                    f"{name}: extraction prompt missing ordered-member linked-target label reuse contract"
                )
            if (
                context.contract.get("required_step_scoped_object_properties")
                and "Required Ordered-Member Object-Link Contract:" not in text
            ):
                failures.append(
                    f"{name}: extraction prompt missing required ordered-member object-link contract"
                )
            if "do not coerce process, event, or relation labels" not in text:
                failures.append(
                    f"{name}: extraction prompt missing class-denotation guard"
                )
            if (
                "multiple linked targets" not in text
                or "formula-like label" not in text
            ):
                failures.append(
                    f"{name}: extraction prompt missing multi-target/formula-like linked target contract"
                )
            if "source species, reagents, aliases, or materials" not in text:
                failures.append(
                    f"{name}: extraction prompt missing source-species object-link contract"
                )
            if "Existing hint labels are not source evidence" not in text:
                failures.append(
                    f"{name}: extraction prompt missing source-evidence-only object-label contract"
                )
            if (
                "structured source labels" not in text
                or "short acronym-like datatype fields" not in text
            ):
                failures.append(
                    f"{name}: extraction prompt missing structured-label/acronym scalar evidence contract"
                )
            if (
                "linked from the current top entity" not in text
                or "same structured source region" not in text
            ):
                failures.append(
                    f"{name}: extraction prompt missing linked-class scalar completeness contract"
                )
            if (
                "positive integers starting at 1" not in text
                or "such as 1.5" not in text
            ):
                failures.append(
                    f"{name}: extraction prompt missing positive-integer ordered-member order contract"
                )
            if "same source operation" not in text:
                failures.append(
                    f"{name}: extraction prompt missing duplicate ordered-operation ban"
                )
            if (
                _mutually_exclusive_property_groups(context)
                and "Mutually Exclusive Property Contract:" not in text
            ):
                failures.append(
                    f"{name}: extraction prompt missing mutually exclusive property contract"
                )
            if "initial introduction members" not in text:
                failures.append(
                    f"{name}: extraction prompt missing initial linked-target ordered-member coverage contract"
                )
            if "dedicated field" not in text or "parameter string" not in text:
                failures.append(
                    f"{name}: extraction prompt missing scalar-specific field extraction contract"
                )
            if any(
                "inherits" in str(prop).lower()
                for prop in (context.parsed.get("properties") or {})
            ):
                if "procedure-inheritance object field" not in text:
                    failures.append(
                        f"{name}: extraction prompt missing procedure-inheritance carry-over contract"
                    )
            if "compact JSON object" not in text:
                failures.append(
                    f"{name}: extraction prompt missing strict compact JSON output contract"
                )
            if (
                re.match(r"EXTRACTION_ITER_\d+_\d+\.md$", name)
                and "directly mergeable into the existing hints" not in text
            ):
                failures.append(
                    f"{name}: enrichment prompt missing mergeable JSON patch contract"
                )
            loose_markers = [
                marker
                for marker in ("ClassLocal", "datatypePropertyLocal")
                if re.search(
                    rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?![A-Za-z0-9_])", text
                )
            ]
            if loose_markers:
                failures.append(
                    f"{name}: extraction prompt still allows loose schema placeholders: {', '.join(loose_markers)}"
                )
        if name.startswith("KG_BUILDING_ITER_"):
            if "Materializable Hint Contract:" not in text:
                failures.append(
                    f"{name}: KG prompt missing materializable hint/tool-parameter contract"
                )
            if (
                "duplicate same-class labels" not in text
                or "unreachable typed nodes" not in text
            ):
                failures.append(
                    f"{name}: KG prompt missing runtime graph hygiene contract for duplicate labels and reachability"
                )
            if "materialize_hints" not in text:
                failures.append(
                    f"{name}: KG prompt missing preferred single-call materialization tool"
                )
            if "pass only the target entity label" not in text:
                failures.append(
                    f"{name}: KG prompt missing pure object-label parameter contract"
                )
            if (
                "must not append the scoped top entity label as a context suffix"
                not in text
            ):
                failures.append(
                    f"{name}: KG prompt missing ban on context-suffixed object-label parameters"
                )
            if "generic ordered-member parent hints as placeholder members" not in text:
                failures.append(
                    f"{name}: KG prompt missing ban on generic ordered-member parent hints"
                )
            if "same source operation" not in text:
                failures.append(
                    f"{name}: KG prompt missing duplicate ordered-operation ban"
                )
            if (
                context.contract.get("required_step_scoped_object_properties")
                and "Required Ordered-Member Object-Link Contract:" not in text
            ):
                failures.append(
                    f"{name}: KG prompt missing required ordered-member object-link contract"
                )
            if (
                _mutually_exclusive_property_groups(context)
                and "Mutually Exclusive Property Contract:" not in text
            ):
                failures.append(
                    f"{name}: KG prompt missing mutually exclusive property contract"
                )
        if name == "KG_BUILDING_ITER_1.md" and context.ontology.role == "main":
            if (
                "pass the source-supported top entity label from inside brackets"
                not in text
            ):
                failures.append(
                    f"{name}: top KG prompt must forbid passing runtime/shell labels to the top create tool"
                )
            if (
                "create only the" not in text
                or "those belong to later per-entity iterations" not in text
            ):
                failures.append(
                    f"{name}: top KG prompt must defer non-top entities and required-link targets to per-entity iterations"
                )
            if "do not create placeholder/shell targets for required links" not in text:
                failures.append(
                    f"{name}: top KG prompt missing ban on placeholder required-link targets"
                )
            if "do not create generic ordered-member targets" not in text:
                failures.append(
                    f"{name}: top KG prompt missing ban on shell ordered-member targets"
                )
        if name == "EXTRACTION_ITER_1.md" and context.ontology.role == "main":
            top_local = (
                str(
                    (context.contract.get("top_entity") or {}).get("class_local")
                    or "Entity"
                ).strip()
                or "Entity"
            )
            top_class = (context.parsed.get("classes") or {}).get(top_local) or {}
            top_comment = str((top_class or {}).get("comment") or "").strip()
            if (
                "T-Box Comment Fidelity Contract:" not in text
                or "binding extraction rule" not in text
            ):
                failures.append(
                    f"{name}: prompt missing generic T-Box comment fidelity contract"
                )
            if "Datatype Properties:" not in text or "Object Properties:" not in text:
                failures.append(
                    f"{name}: prompt missing T-Box property comment sections"
                )
            if re.search(
                r"Return\s+a\s+concise\s+JSON\s+object", text, flags=re.IGNORECASE
            ):
                failures.append(
                    f"{name}: prompt asks for JSON, but pipeline top-entity extraction expects normalized `{top_local}-1 [label]` lines"
                )
            if f"{top_local}-1 [" not in text:
                failures.append(
                    f"{name}: prompt missing pipeline top-entity line format `{top_local}-1 [label]`"
                )
            if "Top-Entity Selection Contract:" not in text:
                failures.append(
                    f"{name}: prompt missing prominent top-entity T-Box selection contract"
                )
            if (
                "Never output the runtime context label `top`" not in text
                or f"`{top_local}-1`" not in text
            ):
                failures.append(
                    f"{name}: prompt must forbid generic runtime/shell labels as top entity labels"
                )
            if top_comment and top_comment[:160] not in text:
                failures.append(
                    f"{name}: prompt does not surface the top-entity class comment near selection rules"
                )
    return failures, warnings


def _foreign_symbol_report(
    context: AgenticGenerationContext,
    foreign_contracts: list[dict[str, Any]] | None,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if not foreign_contracts:
        return failures, warnings
    allowed = {str(x) for x in context.contract.get("ontology_symbol_locals") or []}
    common_tokens = {
        "label",
        "name",
        "type",
        "class",
        "property",
        "comment",
        "range",
        "domain",
        "literal",
        "ontology",
        "document",
    }
    foreign_symbols: set[str] = set()
    for bundle in foreign_contracts:
        foreign_symbols.update(
            str(x) for x in bundle.get("ontology_symbol_locals") or []
        )
    foreign_symbols = {
        s
        for s in foreign_symbols
        if len(s) >= 4
        and s not in allowed
        and s.lower() not in common_tokens
        and not s.islower()
    }
    texts = {
        **_read_texts(Path(context.scripts_dir), "*.py"),
        **_read_texts(Path(context.prompts_dir), "*.md"),
    }
    offenders: list[str] = []
    for name, text in texts.items():
        hits = sorted(
            symbol
            for symbol in foreign_symbols
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", text)
        )
        if hits:
            offenders.append(f"{name}: {', '.join(hits[:8])}")
    if offenders:
        failures.append("Foreign ontology symbols found: " + "; ".join(offenders[:8]))
    return failures, warnings


def build_validation_report(
    context: AgenticGenerationContext,
    *,
    foreign_contracts: list[dict[str, Any]] | None = None,
    write_report: bool = True,
    prompts_required: bool = False,
    extra_failures: list[str] | None = None,
) -> dict[str, Any]:
    scripts_dir = Path(context.scripts_dir)
    prompts_dir = Path(context.prompts_dir)
    failures: list[str] = []
    warnings: list[str] = []
    failures.extend(str(item) for item in (extra_failures or []))
    prompt_files = sorted(prompts_dir.glob("*.md")) if prompts_dir.is_dir() else []
    if prompts_required and not prompt_files:
        failures.append(
            "Prompt enhancement requires existing prompt artifacts; prompt validation cannot be skipped"
        )

    for fn in (
        _syntax_report,
        _expected_tool_surface_report,
        _ordered_member_contract_report,
        _ttl_export_report,
        _prompt_quality_report,
        _medical_csv_roundtrip_prompt_report,
        _runtime_graph_hygiene_report,
    ):
        f, w = fn(context) if fn is not _syntax_report else fn(scripts_dir)
        failures.extend(f)
        warnings.extend(w)

    f, w = _import_report(context)
    failures.extend(f)
    warnings.extend(w)

    if scripts_dir.exists():
        prompts_for_contract = (
            prompts_dir
            if prompts_dir.exists() and list(prompts_dir.glob("*.md"))
            else None
        )
        contract_report = validate_generated_artifacts(
            scripts_dir=scripts_dir,
            prompts_dir=prompts_for_contract,
            contract_bundle=context.contract,
        )
        failures.extend(contract_report.get("failures") or [])
        warnings.extend(contract_report.get("warnings") or [])
    else:
        warnings.append(
            "Contract validation skipped because scripts directory is missing"
        )

    f, w = _foreign_symbol_report(context, foreign_contracts)
    failures.extend(f)
    warnings.extend(w)

    feedback = {
        "coding_agent": [
            msg for msg in failures if "Prompt" not in msg and "prompt" not in msg
        ],
        "prompt_agent": [
            msg
            for msg in failures
            if "Prompt" in msg or "prompt" in msg or "Foreign ontology symbols" in msg
        ],
    }
    report = {
        "ontology": context.ontology.name,
        "ok": not failures,
        "scripts_dir": context.scripts_dir,
        "prompts_dir": context.prompts_dir,
        "failures": failures,
        "warnings": warnings,
        "feedback": feedback,
    }
    if write_report:
        report_path = Path(context.report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return report
