"""Domain-independent RDF graph state and Turtle serialization."""

from __future__ import annotations

import builtins
import importlib
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD


_REGISTRY_NAME = "_twa_generated_rdf_graph_registry"
_PACKAGE_NAMESPACE = __name__.rsplit(".", 1)[0]
_REGISTRY_KEY = f"{Path(__file__).resolve()}::{_PACKAGE_NAMESPACE}"


def _graph_registry() -> dict[str, Graph]:
    registry = getattr(builtins, _REGISTRY_NAME, None)
    if not isinstance(registry, dict):
        registry = {}
        setattr(builtins, _REGISTRY_NAME, registry)
    return registry


def new_graph(*, namespace_bindings: dict[str, str] | None = None) -> Graph:
    """Create a graph with optional stable namespace bindings."""
    graph = Graph()
    for prefix, namespace in sorted((namespace_bindings or {}).items()):
        graph.bind(str(prefix), URIRef(str(namespace)))
    return graph


def retained_graph() -> Graph:
    """Return the process-wide graph retained for this generated package."""
    registry = _graph_registry()
    graph = registry.get(_REGISTRY_KEY)
    if not isinstance(graph, Graph):
        graph = new_graph()
        registry[_REGISTRY_KEY] = graph
    return graph


def reset_retained_graph() -> Graph:
    """Reset and return this generated package's retained graph."""
    return reset_graph(retained_graph())


def initialize_retained_graph(
    *,
    source_path: str | None = None,
) -> dict[str, Any]:
    """Open retained graph state and optionally merge a persisted A-Box."""
    graph = retained_graph()
    before = len(graph)
    loaded = 0
    if source_path:
        result = load_from_turtle_file(source_path, behavior="merge")
        loaded = int(result.get("loaded_triples") or 0)
    return {
        "status": "ok",
        "mode": "open_or_resume",
        "before_triples": before,
        "loaded_triples": loaded,
        "total_triples": len(graph),
    }


def safe_filename_component(value: str) -> str:
    """Normalize a runtime scope using the pipeline's canonical filename policy."""
    normalized = unicodedata.normalize("NFKC", str(value or "").strip())
    chars: list[str] = []
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


def resolve_case_dirname(doi_value: str) -> str:
    """Resolve a DOI/document identifier to the pipeline's canonical case folder."""
    raw = str(doi_value or "").strip() or "unknown"
    safe = safe_filename_component(raw)
    root = Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data")
    mapping_path = root / "doi_to_hash.json"
    if not mapping_path.exists():
        return safe
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    except Exception:
        return safe
    hashes = {str(value).strip() for value in mapping.values() if str(value).strip()}
    if safe in hashes:
        return safe
    candidates = {
        raw,
        safe,
        raw.replace("_", "/"),
        raw.replace("/", "_"),
        safe.replace("_", "/"),
    }
    for doi_key, hash_value in mapping.items():
        key = str(doi_key or "").strip()
        hashed = str(hash_value or "").strip()
        if not key or not hashed:
            continue
        key_us = key.replace("/", "_")
        if (
            key in candidates
            or key_us in candidates
            or safe_filename_component(key_us) == safe
        ):
            return hashed
    return safe


def scoped_memory_paths(
    doi: str,
    top_level_entity_name: str,
) -> tuple[Path, Path]:
    """Return canonical memory and timestamped export paths for one scope."""
    root = Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data")
    case_dir = root / resolve_case_dirname(doi)
    memory_dir = case_dir / "memory"
    exports_dir = case_dir / "exports"
    memory_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    safe_entity = safe_filename_component(top_level_entity_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return (
        memory_dir / f"{safe_entity}.ttl",
        exports_dir / f"{safe_entity}_{timestamp}.ttl",
    )


def init_memory(doi: str, top_level_entity_name: str) -> str:
    """Open or resume one canonical retained-graph scope without clearing state."""
    memory_path, export_path = scoped_memory_paths(doi, top_level_entity_name)
    if memory_path.is_file():
        load_state = initialize_retained_graph(source_path=str(memory_path))
    else:
        load_state = initialize_retained_graph()
    return result_json(
        success_result(
            message="Initialized memory",
            doi=doi,
            top_level_entity_name=top_level_entity_name,
            memory_path=str(memory_path),
            export_path=str(export_path),
            load_state=load_state,
        )
    )


def export_memory(doi: str, top_level_entity_name: str) -> str:
    """Persist and return the current scope's A-Box-only graph."""
    return result_json(
        export_graph_result(
            retained_graph(),
            doi=doi,
            scope=top_level_entity_name,
        )
    )


def load_from_turtle_file(path: str, behavior: str = "merge") -> dict[str, Any]:
    """Load a Turtle artifact into the retained graph for cross-process resume."""
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"Turtle source is not a file: {source}")
    normalized_behavior = str(behavior).strip().casefold()
    if normalized_behavior not in {"merge", "replace"}:
        raise ValueError("behavior must be `merge` or `replace`")
    graph = retained_graph()
    if normalized_behavior == "replace":
        reset_graph(graph)
    before = len(graph)
    graph.parse(source, format="turtle")
    return {
        "status": "ok",
        "path": str(source),
        "behavior": normalized_behavior,
        "loaded_triples": len(graph) - before,
        "total_triples": len(graph),
    }


class RelationshipContractError(ValueError):
    """Structured rejection raised before an invalid relationship can mutate RDF."""

    def __init__(self, code: str, details: dict[str, Any]) -> None:
        self.code = str(code)
        self.details = dict(details)
        super().__init__(
            json.dumps(
                {"status": "rejected", "code": self.code, **self.details},
                sort_keys=True,
            )
        )


def _compatible_type(
    actual_types: set[str],
    expected_types: set[str],
    subclass_closure: dict[str, set[str]],
) -> bool:
    return any(
        actual == expected
        or expected in subclass_closure.get(actual, {actual})
        for actual in actual_types
        for expected in expected_types
    )


def _bound_relationship_writer(
    *,
    predicate_iri: str,
    domain_iris: set[str],
    range_iris: set[str],
    subclass_closure: dict[str, set[str]],
) -> Callable[[str, str], dict[str, Any]]:
    """Create one private mutation capability with an immutable T-Box contract."""
    predicate = URIRef(predicate_iri)

    def write(subject_iri: str, object_iri: str) -> dict[str, Any]:
        graph = retained_graph()
        subject = URIRef(str(subject_iri))
        obj = URIRef(str(object_iri))
        subject_types = {str(value) for value in graph.objects(subject, RDF.type)}
        object_types = {str(value) for value in graph.objects(obj, RDF.type)}
        checks = (
            ("SUBJECT_TYPE_MISSING", subject_types, domain_iris, "domain"),
            ("OBJECT_TYPE_MISSING", object_types, range_iris, "range"),
        )
        for missing_code, actual, expected, role in checks:
            if not actual:
                raise RelationshipContractError(
                    missing_code,
                    {
                        "predicate_iri": predicate_iri,
                        f"{role}_iris": sorted(expected),
                        f"{role[:-1] if role.endswith('s') else role}_node": (
                            str(subject) if role == "domain" else str(obj)
                        ),
                    },
                )
            if expected and not _compatible_type(actual, expected, subclass_closure):
                raise RelationshipContractError(
                    f"{role.upper()}_TYPE_MISMATCH",
                    {
                        "predicate_iri": predicate_iri,
                        "actual_type_iris": sorted(actual),
                        f"expected_{role}_iris": sorted(expected),
                    },
                )
        graph.add((subject, predicate, obj))
        return {
            "status": "ok",
            "action": "add_relationship",
            "triple": [str(subject), predicate_iri, str(obj)],
        }

    return write


def _compile_relationship_capabilities(
    ontology_contract: dict[str, Any],
) -> dict[str, Callable[[str, str], dict[str, Any]]]:
    """Compile property-specific, fail-closed writers from a T-Box contract."""
    closure = {
        str(item.get("class_iri") or ""): {
            str(value) for value in item.get("superclass_iris") or []
        }
        for item in ontology_contract.get("subclass_closure") or []
        if str(item.get("class_iri") or "")
    }
    capabilities: dict[str, Callable[[str, str], dict[str, Any]]] = {}
    for item in ontology_contract.get("object_properties") or []:
        predicate_iri = str(item.get("property_iri") or "").strip()
        domain_iris = {str(value) for value in item.get("domain_iris") or []}
        range_iris = {str(value) for value in item.get("range_iris") or []}
        if not predicate_iri or not domain_iris or not range_iris:
            continue
        capabilities[predicate_iri] = _bound_relationship_writer(
            predicate_iri=predicate_iri,
            domain_iris=domain_iris,
            range_iris=range_iris,
            subclass_closure=closure,
        )
    return capabilities


def package_relationship_capabilities() -> dict[
    str, Callable[[str, str], dict[str, Any]]
]:
    """Load immutable relationship capabilities shipped with this package."""
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package relationship contract must be a JSON object")
    return _compile_relationship_capabilities(contract)


def _bound_entity_creator(
    class_iri: str,
    *,
    explicit_type_iris: set[str] | None = None,
    strict_subclass_iris: set[str] | None = None,
) -> Callable[[str], str]:
    """Create one private entity capability with an immutable T-Box class."""
    class_ref = URIRef(class_iri)
    type_refs = {
        URIRef(type_iri)
        for type_iri in (explicit_type_iris or {class_iri})
        if str(type_iri).strip()
    }
    type_refs.add(class_ref)
    strict_subclass_refs = {
        URIRef(value)
        for value in (strict_subclass_iris or set())
        if str(value).strip() and str(value) != class_iri
    }

    def create(label: str) -> str:
        if not isinstance(label, str):
            raise RelationshipContractError(
                "INVALID_ENTITY_LABEL_TYPE",
                {
                    "class_iri": class_iri,
                    "actual_python_type": type(label).__name__,
                },
            )
        normalized_label = label.strip()
        if not normalized_label:
            raise RelationshipContractError(
                "EMPTY_ENTITY_LABEL",
                {"class_iri": class_iri},
            )
        graph = retained_graph()
        for subject in graph.subjects(RDF.type, class_ref):
            if any(
                (subject, RDF.type, subclass_ref) in graph
                for subclass_ref in strict_subclass_refs
            ):
                continue
            if any(
                str(value).strip() == normalized_label
                for value in graph.objects(subject, RDFS.label)
            ):
                return str(subject)
        iri = f"urn:uuid:{uuid4()}"
        subject = URIRef(iri)
        for type_ref in sorted(type_refs, key=str):
            graph.add((subject, RDF.type, type_ref))
        graph.add((subject, RDFS.label, Literal(normalized_label)))
        return iri

    return create


def _compile_entity_capabilities(
    ontology_contract: dict[str, Any],
) -> dict[str, Callable[[str], str]]:
    """Compile class-specific creators only for T-Box-declared classes."""
    closure = {
        str(item.get("class_iri") or ""): {
            str(value)
            for value in item.get("superclass_iris") or []
            if str(value).strip()
        }
        for item in ontology_contract.get("subclass_closure") or []
        if str(item.get("class_iri") or "").strip()
    }
    strict_subclasses = {
        class_iri: {
            candidate_iri
            for candidate_iri, superclass_iris in closure.items()
            if candidate_iri != class_iri and class_iri in superclass_iris
        }
        for class_iri in closure
    }
    return {
        class_iri: _bound_entity_creator(
            class_iri,
            explicit_type_iris=closure.get(class_iri, {class_iri}),
            strict_subclass_iris=strict_subclasses.get(class_iri, set()),
        )
        for item in ontology_contract.get("classes") or []
        if (class_iri := str(item.get("class_iri") or "").strip())
    }


def package_entity_capabilities() -> dict[str, Callable[[str], str]]:
    """Load immutable class-creation capabilities shipped with this package."""
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package ontology contract must be a JSON object")
    return _compile_entity_capabilities(contract)


def _compile_ordered_entity_capabilities(
    ontology_contract: dict[str, Any],
) -> dict[str, Callable[[str, int], str]]:
    """Compile atomic label-plus-order creators from a T-Box-derived contract."""
    entity_capabilities = _compile_entity_capabilities(ontology_contract)
    datatype_capabilities = _compile_datatype_capabilities(ontology_contract)
    capabilities: dict[str, Callable[[str, int], str]] = {}
    for item in ontology_contract.get("ordered_entity_creators") or []:
        class_iri = str(item.get("class_iri") or "").strip()
        ordering_property_iri = str(
            item.get("ordering_property_iri") or ""
        ).strip()
        entity_creator = entity_capabilities.get(class_iri)
        order_writer = datatype_capabilities.get(ordering_property_iri)
        if not class_iri or entity_creator is None or order_writer is None:
            continue

        def create(
            label: str,
            order: int,
            *,
            _entity_creator: Callable[[str], str] = entity_creator,
            _order_writer: Callable[[str, Any], dict[str, Any]] = order_writer,
            _class_iri: str = class_iri,
        ) -> str:
            if isinstance(order, bool) or not isinstance(order, int) or order < 1:
                raise RelationshipContractError(
                    "INVALID_ORDER",
                    {
                        "class_iri": _class_iri,
                        "order": order,
                        "requirement": "positive integer",
                    },
                )
            graph = retained_graph()
            before = set(graph)
            try:
                iri = _entity_creator(label)
                _order_writer(iri, order)
                return iri
            except BaseException:
                graph.remove((None, None, None))
                for triple in before:
                    graph.add(triple)
                raise

        capabilities[class_iri] = create
    return capabilities


def package_ordered_entity_capabilities() -> dict[
    str, Callable[[str, int], str]
]:
    """Load atomic ordered creators compiled from this package's T-Box contract."""
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package ontology contract must be a JSON object")
    return _compile_ordered_entity_capabilities(contract)


def _om2_quantity_range_iris(ontology_contract: dict[str, Any]) -> set[str]:
    """Return OM-2 classes authorized as object-property ranges."""
    marker = "ontology-of-units-of-measure.org/resource/om-2/"
    return {
        str(range_iri)
        for item in ontology_contract.get("object_properties") or []
        for range_iri in item.get("range_iris") or []
        if marker in str(range_iri)
    }


def package_om2_quantity_creator() -> Callable[[str, str], str]:
    """Create a T-Box-range-bounded OM-2 quantity creator for this package."""
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package ontology contract must be a JSON object")
    allowed_class_iris = _om2_quantity_range_iris(contract)
    try:
        om2_runtime = importlib.import_module(f"{__package__}._fixed_om2_runtime")
    except ModuleNotFoundError:
        om2_runtime = importlib.import_module(f"{__package__}.fixed_om2_runtime")
    quantity_from_label = om2_runtime.find_or_create_om2_quantity_from_label

    def create(quantity_class_iri: str, label: str) -> str:
        normalized_class_iri = str(quantity_class_iri or "").strip()
        if normalized_class_iri not in allowed_class_iris:
            raise RelationshipContractError(
                "OM2_QUANTITY_CLASS_NOT_ALLOWED",
                {
                    "quantity_class_iri": normalized_class_iri,
                    "allowed_class_iris": sorted(allowed_class_iris),
                },
            )
        if not isinstance(label, str) or not label.strip():
            raise RelationshipContractError(
                "INVALID_OM2_QUANTITY_LABEL",
                {"quantity_class_iri": normalized_class_iri},
            )

        def mint_iri(class_local: str, source_label: str) -> URIRef:
            return URIRef(f"urn:uuid:{uuid4()}")

        return str(
            quantity_from_label(
                retained_graph(),
                quantity_class=URIRef(normalized_class_iri),
                label=label.strip(),
                mint_iri=mint_iri,
            )
        )

    return create


def create_om2_quantity(quantity_class_iri: str, label: str) -> str:
    """Public fixed adapter for bounded OM-2 quantity creation."""
    try:
        iri = package_om2_quantity_creator()(quantity_class_iri, label)
    except RelationshipContractError as exc:
        return error_json(
            code=exc.code,
            message="OM-2 quantity creation rejected by the package contract.",
            **exc.details,
        )
    except ValueError as exc:
        return error_json(
            code="INVALID_OM2_QUANTITY",
            message=str(exc),
        )
    return success_json(
        iri=iri,
        message="Created or reused OM-2 quantity.",
    )


_PYTHON_DATATYPES: dict[str, tuple[type, ...]] = {
    str(XSD.string): (str,),
    str(XSD.integer): (int,),
    str(XSD.int): (int,),
    str(XSD.decimal): (int, float),
    str(XSD.double): (int, float),
    str(XSD.float): (int, float),
    str(XSD.boolean): (bool,),
}


def _bound_datatype_writer(
    *,
    predicate_iri: str,
    domain_iris: set[str],
    range_iri: str,
    subclass_closure: dict[str, set[str]],
) -> Callable[[str, Any], dict[str, Any]]:
    """Create one private literal capability with immutable T-Box semantics."""
    predicate = URIRef(predicate_iri)
    expected_python = _PYTHON_DATATYPES.get(range_iri)

    def write(subject_iri: str, value: Any) -> dict[str, Any]:
        graph = retained_graph()
        subject = URIRef(str(subject_iri))
        subject_types = {str(item) for item in graph.objects(subject, RDF.type)}
        if not subject_types:
            raise RelationshipContractError(
                "SUBJECT_TYPE_MISSING",
                {"predicate_iri": predicate_iri, "subject_iri": str(subject)},
            )
        if not _compatible_type(subject_types, domain_iris, subclass_closure):
            raise RelationshipContractError(
                "DOMAIN_TYPE_MISMATCH",
                {
                    "predicate_iri": predicate_iri,
                    "actual_type_iris": sorted(subject_types),
                    "expected_domain_iris": sorted(domain_iris),
                },
            )
        invalid_bool_subclass = isinstance(value, bool) and bool not in expected_python
        if invalid_bool_subclass or not isinstance(value, expected_python):
            raise RelationshipContractError(
                "DATATYPE_MISMATCH",
                {
                    "predicate_iri": predicate_iri,
                    "expected_range_iri": range_iri,
                    "actual_python_type": type(value).__name__,
                },
            )
        # Datatype setters have replace/exactly-one semantics. This prevents
        # retries or corrected values from leaving conflicting literals behind.
        graph.remove((subject, predicate, None))
        graph.add((subject, predicate, Literal(value, datatype=URIRef(range_iri))))
        return {
            "status": "ok",
            "action": "set_datatype_property",
            "subject_iri": str(subject),
            "predicate_iri": predicate_iri,
        }

    return write


def _compile_datatype_capabilities(
    ontology_contract: dict[str, Any],
) -> dict[str, Callable[[str, Any], dict[str, Any]]]:
    """Compile property-specific literal writers from complete T-Box contracts."""
    closure = {
        str(item.get("class_iri") or ""): {
            str(value) for value in item.get("superclass_iris") or []
        }
        for item in ontology_contract.get("subclass_closure") or []
        if str(item.get("class_iri") or "")
    }
    capabilities: dict[str, Callable[[str, Any], dict[str, Any]]] = {}
    for item in ontology_contract.get("datatype_properties") or []:
        predicate_iri = str(item.get("property_iri") or "").strip()
        domain_iris = {str(value) for value in item.get("domain_iris") or []}
        range_iris = [str(value) for value in item.get("range_iris") or []]
        if (
            not predicate_iri
            or not domain_iris
            or len(range_iris) != 1
            or range_iris[0] not in _PYTHON_DATATYPES
        ):
            continue
        capabilities[predicate_iri] = _bound_datatype_writer(
            predicate_iri=predicate_iri,
            domain_iris=domain_iris,
            range_iri=range_iris[0],
            subclass_closure=closure,
        )
    return capabilities


def package_datatype_capabilities() -> dict[
    str, Callable[[str, Any], dict[str, Any]]
]:
    """Load immutable datatype capabilities shipped with this package."""
    contract_path = Path(__file__).with_name("_relationship_contract.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package ontology contract must be a JSON object")
    return _compile_datatype_capabilities(contract)


def reset_graph(graph: Graph) -> Graph:
    """Remove all triples while preserving the graph object used by callers."""
    graph.remove((None, None, None))
    return graph


def serialize_turtle(graph: Graph) -> str:
    """Serialize a graph to normalized UTF-8 Turtle text."""
    serialized = graph.serialize(format="turtle")
    if isinstance(serialized, bytes):
        return serialized.decode("utf-8")
    return str(serialized)


def abox_graph(graph: Graph) -> Graph:
    """Return asserted instance facts while excluding embedded schema triples."""
    schema_types = {
        OWL.Class,
        RDFS.Class,
        OWL.ObjectProperty,
        OWL.DatatypeProperty,
        OWL.AnnotationProperty,
        OWL.Ontology,
        RDF.Property,
    }
    schema_subjects = {
        subject
        for subject, _, obj in graph.triples((None, RDF.type, None))
        if obj in schema_types
    }
    schema_predicates = {
        RDF.first,
        RDF.rest,
        RDFS.domain,
        RDFS.range,
        RDFS.subClassOf,
        RDFS.subPropertyOf,
        OWL.intersectionOf,
        OWL.equivalentClass,
        OWL.equivalentProperty,
        OWL.inverseOf,
    }
    result = new_graph(
        namespace_bindings={prefix: str(namespace) for prefix, namespace in graph.namespaces()}
    )
    for subject, predicate, obj in graph:
        if (
            isinstance(subject, BNode)
            or subject in schema_subjects
            or predicate in schema_predicates
        ):
            continue
        result.add((subject, predicate, obj))
    return result


def export_graph_result(
    graph: Graph,
    *,
    top_iri: str | URIRef | None = None,
    status: str = "ok",
    include_schema: bool = False,
    **metadata: Any,
) -> dict[str, Any]:
    """Export A-Box data and persist it when runtime scope metadata is supplied."""
    exported_graph = graph if include_schema else abox_graph(graph)
    ttl = serialize_turtle(exported_graph)
    safe_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in {"status", "top_iri", "ttl", "triple_count", "includes_schema"}
    }
    doi = str(metadata.get("doi") or "").strip()
    scope = str(
        metadata.get("scope")
        or metadata.get("top_level_entity_name")
        or ""
    ).strip()
    if doi and scope:
        memory_path, export_path = scoped_memory_paths(doi, scope)
        memory_path.write_text(ttl, encoding="utf-8")
        export_path.write_text(ttl, encoding="utf-8")
        safe_metadata["memory_path"] = str(memory_path)
        safe_metadata["export_path"] = str(export_path)
    return {
        "status": str(status),
        "top_iri": str(top_iri or ""),
        "ttl": ttl,
        "triple_count": len(exported_graph),
        "includes_schema": bool(include_schema),
        **safe_metadata,
    }


def success_result(
    *,
    iri: str = "",
    message: str = "",
    **metadata: Any,
) -> dict[str, Any]:
    """Return the shared JSON-safe success envelope for generated tools."""
    return {
        "status": "ok",
        "iri": str(iri),
        "message": str(message),
        **metadata,
    }


def error_result(
    *,
    code: str,
    message: str,
    **metadata: Any,
) -> dict[str, Any]:
    """Return the shared JSON-safe error envelope for generated tools."""
    return {
        "status": "rejected",
        "code": str(code),
        "message": str(message),
        **metadata,
    }


def result_json(result: dict[str, Any]) -> str:
    """Serialize one standard result envelope for public MCP tool transport."""
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def success_json(
    *,
    iri: str = "",
    message: str = "",
    **metadata: Any,
) -> str:
    """Return a serialized standard success envelope for public MCP tools."""
    return result_json(
        success_result(iri=iri, message=message, **metadata)
    )


def error_json(
    *,
    code: str,
    message: str,
    **metadata: Any,
) -> str:
    """Return a serialized standard rejection envelope for public MCP tools."""
    return result_json(
        error_result(code=code, message=message, **metadata)
    )
