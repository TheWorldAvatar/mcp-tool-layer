"""Relationship, entity, datatype, and OM-2 writers compiled from T-Box.

Bound at package import from the publish contract. See rdf/README.md.
"""

from __future__ import annotations

import importlib
import json
from typing import Any, Callable

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, XSD

from .constants import (
    RelationshipContractError,
    om2_runtime_package,
    relationship_contract_path,
    sanitize_tool_text,
    _short_random_iri,
)
from .envelopes import error_json, success_json
from .lifecycle import _enrichment_targets_from_global_state
from .registry import bound_root_iri, retained_graph
from .reuse import (
    _validate_central_reuse_authorization,
    load_central_reuse_memory,
    load_document_reuse_memory,
)


def _compatible_type(
    actual_types: set[str],
    expected_types: set[str],
    subclass_closure: dict[str, set[str]],
) -> bool:
    return any(
        actual == expected or expected in subclass_closure.get(actual, {actual})
        for actual in actual_types
        for expected in expected_types
    )


def _typed_subject_candidates(
    graph: Graph,
    expected_types: set[str],
    subclass_closure: dict[str, set[str]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """List scope-local subjects compatible with an expected domain."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    bound = bound_root_iri()
    for subject in graph.subjects(RDF.type, None):
        if not isinstance(subject, URIRef) or str(subject) in seen:
            continue
        actual_types = {
            str(value)
            for value in graph.objects(subject, RDF.type)
            if isinstance(value, URIRef)
        }
        if expected_types and not _compatible_type(
            actual_types,
            expected_types,
            subclass_closure,
        ):
            continue
        iri = str(subject)
        seen.add(iri)
        candidates.append(
            {
                "iri": iri,
                "type_iris": sorted(actual_types),
                "labels": sorted(
                    {
                        str(value)
                        for value in graph.objects(subject, RDFS.label)
                        if str(value).strip()
                    }
                )[:3],
                "is_bound_root": bool(bound and iri == bound),
            }
        )
    candidates.sort(
        key=lambda item: (
            not bool(item.get("is_bound_root")),
            str(item.get("iri") or ""),
        )
    )
    return candidates[: max(1, int(limit))]


def _hydrate_reusable_object_from_central_memory(
    *,
    graph: Graph,
    obj: URIRef,
    ontology_name: str,
    reusable_class_iris: set[str],
    document_reusable_class_iris: set[str],
    expected_range_iris: set[str],
    subclass_closure: dict[str, set[str]],
    reuse_authorization_token: str | None,
) -> dict[str, Any] | None:
    """Project trusted descriptors from the policy-routed reuse store."""
    if (
        not ontology_name
        or not (reusable_class_iris or document_reusable_class_iris)
        or not expected_range_iris
    ):
        return None
    central, _ = load_central_reuse_memory(ontology_name)
    candidate_graph = central
    allowed_class_iris = reusable_class_iris
    candidate_types = {
        str(value)
        for value in candidate_graph.objects(obj, RDF.type)
        if isinstance(value, URIRef)
    }
    source = "central_memory"
    if not candidate_types.intersection(allowed_class_iris):
        document, _ = load_document_reuse_memory(ontology_name)
        candidate_graph = document
        allowed_class_iris = document_reusable_class_iris
        candidate_types = {
            str(value)
            for value in candidate_graph.objects(obj, RDF.type)
            if isinstance(value, URIRef)
        }
        source = "document_memory"
    if not candidate_types.intersection(allowed_class_iris):
        return None
    if not _compatible_type(
        candidate_types,
        expected_range_iris,
        subclass_closure,
    ):
        return None
    grant = _validate_central_reuse_authorization(
        str(obj),
        reuse_authorization_token,
    )

    hydrated_types = sorted(candidate_types)
    hydrated_labels = sorted(
        {
            str(value)
            for value in candidate_graph.objects(obj, RDFS.label)
            if isinstance(value, Literal) and str(value).strip()
        }
    )
    for type_iri in hydrated_types:
        graph.add((obj, RDF.type, URIRef(type_iri)))
    for label in hydrated_labels:
        graph.add((obj, RDFS.label, Literal(label)))
    return {
        "object_type_source": source,
        "hydrated_type_iris": hydrated_types,
        "hydrated_labels": hydrated_labels,
        "reuse_authorization_pair_id": grant["pair_id"],
    }


def _clone_occurrence_local_object(graph: Graph, source: URIRef) -> URIRef:
    """Mint a shallow clone of an occurrence-local node for a new owner slot.

    Incoming owner links stay on ``source``. Outgoing descriptors (types, labels,
    values, units, and other attributes) are copied onto a fresh IRI so equal
    values can satisfy a second ordered member without sharing identity.
    """
    clone = URIRef(_short_random_iri())
    for predicate, value in graph.predicate_objects(source):
        graph.add((clone, predicate, value))
    return clone


def _bound_relationship_writer(
    *,
    predicate_iri: str,
    domain_iris: set[str],
    range_iris: set[str],
    subclass_closure: dict[str, set[str]],
    relationship_specs: list[dict[str, Any]] | None = None,
    creator_owned_relationships: dict[str, list[dict[str, str]]] | None = None,
    ontology_name: str = "",
    reusable_class_iris: set[str] | None = None,
    document_reusable_class_iris: set[str] | None = None,
    non_reusable_class_iris: set[str] | None = None,
    ordered_member_class_iris: set[str] | None = None,
) -> Callable[[str, str, str | None], dict[str, Any]]:
    """Create one private mutation capability with an immutable T-Box contract."""
    predicate = URIRef(predicate_iri)
    reusable_classes = set(reusable_class_iris or set())
    document_reusable_classes = set(document_reusable_class_iris or set())
    non_reusable_classes = set(non_reusable_class_iris or set())
    ordered_member_classes = set(ordered_member_class_iris or set())
    available_relationship_specs = list(relationship_specs or [])
    atomic_owners = dict(creator_owned_relationships or {})

    def write(
        subject_iri: str,
        object_iri: str,
        reuse_authorization_token: str | None = None,
    ) -> dict[str, Any]:
        graph = retained_graph()
        subject = URIRef(str(subject_iri))
        obj = URIRef(str(object_iri))
        subject_types = {str(value) for value in graph.objects(subject, RDF.type)}
        object_types = {str(value) for value in graph.objects(obj, RDF.type)}

        def compatible_bindings() -> list[dict[str, Any]]:
            bindings: list[dict[str, Any]] = []
            if not subject_types or not object_types:
                return bindings
            for spec in available_relationship_specs:
                candidate_iri = str(spec.get("property_iri") or "").strip()
                candidate_domains = {
                    str(value) for value in spec.get("domain_iris") or []
                }
                candidate_ranges = {
                    str(value) for value in spec.get("range_iris") or []
                }
                if (
                    not candidate_iri
                    or not candidate_domains
                    or not candidate_ranges
                    or not _compatible_type(
                        subject_types, candidate_domains, subclass_closure
                    )
                    or not _compatible_type(
                        object_types, candidate_ranges, subclass_closure
                    )
                ):
                    continue
                owners = atomic_owners.get(candidate_iri) or []
                local = candidate_iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
                bindings.append(
                    {
                        "predicate_iri": candidate_iri,
                        "operation": (
                            {
                                "mode": "atomic_creator",
                                "public_tools": sorted(
                                    {
                                        str(owner.get("public_tool") or "")
                                        for owner in owners
                                        if str(owner.get("public_tool") or "")
                                    }
                                ),
                                "instruction": (
                                    "This edge is creator-owned. If its owner was already created "
                                    "successfully, the binding is already complete; do not call or "
                                    "invent a standalone relationship writer."
                                ),
                            }
                            if owners
                            else {
                                "mode": "standalone_relationship",
                                "public_tool": f"add_{local}",
                            }
                        ),
                    }
                )
            return sorted(bindings, key=lambda item: item["predicate_iri"])

        if not subject_types:
            raise RelationshipContractError(
                "SUBJECT_TYPE_MISSING",
                {
                    "predicate_iri": predicate_iri,
                    "domain_iris": sorted(domain_iris),
                    "domain_node": str(subject),
                    "skippable": False,
                    "retryable": True,
                    "recovery": {
                        "action": "use_bound_root_or_candidate_subject",
                        "bound_root_iri": bound_root_iri(),
                        "candidate_subjects": _typed_subject_candidates(
                            graph,
                            domain_iris,
                            subclass_closure,
                        ),
                        "instruction": (
                            "Retry with the bound root when this is a root-owned "
                            "operation; otherwise choose only a source-grounded "
                            "candidate from the current session."
                        ),
                    },
                },
            )
        if domain_iris and not _compatible_type(
            subject_types,
            domain_iris,
            subclass_closure,
        ):
            raise RelationshipContractError(
                "DOMAIN_TYPE_MISMATCH",
                {
                    "predicate_iri": predicate_iri,
                    "skippable": True,
                    "recovery": {
                        "action": "use_compatible_binding_or_skip_relationship",
                        "do_not_retry_subject_iri": str(subject),
                        "compatible_bindings": compatible_bindings(),
                        "instruction": (
                            "Do not retry the rejected predicate with this subject or another "
                            "subject of the same type. Follow compatible_bindings when non-empty. "
                            "For an atomic_creator binding, a successful owner creator already "
                            "completed the edge. Otherwise use the listed standalone tool. If no "
                            "source-grounded compatible binding applies, skip this relationship "
                            "and continue remaining obligations without repeating prior successes."
                        ),
                    },
                    "actual_type_iris": sorted(subject_types),
                    "expected_domain_iris": sorted(domain_iris),
                },
            )

        central_projection = None
        if not object_types:
            central_projection = _hydrate_reusable_object_from_central_memory(
                graph=graph,
                obj=obj,
                ontology_name=ontology_name,
                reusable_class_iris=reusable_classes,
                document_reusable_class_iris=document_reusable_classes,
                expected_range_iris=range_iris,
                subclass_closure=subclass_closure,
                reuse_authorization_token=reuse_authorization_token,
            )
            object_types = {
                str(value) for value in graph.objects(obj, RDF.type)
            }
        if not object_types:
            raise RelationshipContractError(
                "OBJECT_TYPE_MISSING",
                {
                    "predicate_iri": predicate_iri,
                    "range_iris": sorted(range_iris),
                    "range_node": str(obj),
                    "skippable": True,
                    "retryable": True,
                    "recovery": {
                        "action": "use_candidate_object_or_skip_relationship",
                        "candidate_objects": _typed_subject_candidates(
                            graph,
                            range_iris,
                            subclass_closure,
                        ),
                    },
                },
            )
        if range_iris and not _compatible_type(
            object_types,
            range_iris,
            subclass_closure,
        ):
            raise RelationshipContractError(
                "RANGE_TYPE_MISMATCH",
                {
                    "predicate_iri": predicate_iri,
                    "skippable": True,
                    "recovery": {
                        "action": "use_compatible_binding_or_skip_relationship",
                        "do_not_retry_object_iri": str(obj),
                        "compatible_bindings": compatible_bindings(),
                        "instruction": (
                            "Do not retry the rejected predicate with this object or another "
                            "object of the same type. Follow compatible_bindings when non-empty. "
                            "For an atomic_creator binding, a successful owner creator already "
                            "completed the edge. Otherwise use the listed standalone tool. If no "
                            "source-grounded compatible binding applies, skip this relationship "
                            "and continue remaining obligations without repeating prior successes."
                        ),
                    },
                    "actual_type_iris": sorted(object_types),
                    "expected_range_iris": sorted(range_iris),
                },
            )
        object_is_non_reusable = bool(
            non_reusable_classes
            and _compatible_type(
                object_types,
                non_reusable_classes,
                subclass_closure,
            )
        )
        subject_is_ordered_member = bool(
            ordered_member_classes
            and _compatible_type(
                subject_types,
                ordered_member_classes,
                subclass_closure,
            )
        )
        clone_meta: dict[str, Any] | None = None
        if object_is_non_reusable and subject_is_ordered_member:
            conflicting_uses = sorted(
                {
                    (str(existing_subject), str(existing_predicate))
                    for existing_subject, existing_predicate in graph.subject_predicates(
                        obj
                    )
                    if _compatible_type(
                        {
                            str(value)
                            for value in graph.objects(existing_subject, RDF.type)
                        },
                        ordered_member_classes,
                        subclass_closure,
                    )
                    # Non-reusability prevents one occurrence from filling the
                    # same semantic role for multiple ordered members. It does
                    # not prohibit an exact occurrence from participating in a
                    # different T-Box relationship role.
                    and existing_predicate == predicate
                    and existing_subject != subject
                }
            )
            if conflicting_uses:
                requested_object_iri = str(obj)
                obj = _clone_occurrence_local_object(graph, obj)
                clone_meta = {
                    "auto_cloned_occurrence": True,
                    "requested_object_iri": requested_object_iri,
                    "cloned_object_iri": str(obj),
                    "reason": "OBJECT_OCCURRENCE_REUSE_FORBIDDEN",
                    "conflicting_uses": [
                        {
                            "subject_iri": existing_subject,
                            "predicate_iri": existing_predicate,
                        }
                        for existing_subject, existing_predicate in conflicting_uses
                    ],
                    "message": (
                        "Requested object was already bound to another ordered "
                        "member for this predicate. Minted a fresh occurrence-local "
                        "clone for this owner so the relationship could proceed."
                    ),
                }
        graph.add((subject, predicate, obj))
        result = {
            "status": "ok",
            "action": "add_relationship",
            "triple": [str(subject), predicate_iri, str(obj)],
        }
        if clone_meta:
            result.update(clone_meta)
        if central_projection:
            result.update(central_projection)
        return result

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
    reuse_policy = ontology_contract.get("reuse_policy") or {}
    reusable_class_iris = {
        str(item.get("class_iri") or "").strip()
        for item in reuse_policy.get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is True
        and str(item.get("class_iri") or "").strip()
        and str(item.get("reuse_scope") or "legacy_unspecified").strip()
        in {"global", "global_value", "global_reference", "legacy_unspecified"}
    }
    document_reusable_class_iris = {
        str(item.get("class_iri") or "").strip()
        for item in reuse_policy.get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is True
        and str(item.get("class_iri") or "").strip()
        and str(item.get("reuse_scope") or "").strip() == "document"
    }
    non_reusable_class_iris = {
        str(item.get("class_iri") or "").strip()
        for item in reuse_policy.get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is False
        and str(item.get("class_iri") or "").strip()
    }
    ordered_member_class_iris = {
        str(item.get("class_iri") or "").strip()
        for item in ontology_contract.get("ordered_entity_creators") or []
        if isinstance(item, dict)
        and str(item.get("class_iri") or "").strip()
    }
    ontology_name = str(ontology_contract.get("ontology_name") or "").strip()
    relationship_specs = [
        dict(item)
        for item in ontology_contract.get("object_properties") or []
        if isinstance(item, dict)
    ]
    creator_owned_relationships = {
        str(predicate_iri): [
            dict(owner)
            for owner in owners or []
            if isinstance(owner, dict)
        ]
        for predicate_iri, owners in (
            ontology_contract.get("creator_owned_relationships") or {}
        ).items()
    }
    capabilities: dict[str, Callable[[str, str], dict[str, Any]]] = {}
    for item in relationship_specs:
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
            relationship_specs=relationship_specs,
            creator_owned_relationships=creator_owned_relationships,
            ontology_name=ontology_name,
            reusable_class_iris=reusable_class_iris,
            document_reusable_class_iris=document_reusable_class_iris,
            non_reusable_class_iris=non_reusable_class_iris,
            ordered_member_class_iris=ordered_member_class_iris,
        )
    return capabilities


def package_relationship_capabilities() -> dict[
    str, Callable[[str, str], dict[str, Any]]
]:
    """Load immutable relationship capabilities shipped with this package."""
    contract_path = relationship_contract_path()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package relationship contract must be a JSON object")
    return _compile_relationship_capabilities(contract)


def _bound_entity_creator(
    class_iri: str,
    *,
    explicit_type_iris: set[str] | None = None,
    strict_subclass_iris: set[str] | None = None,
    reuse_by_label: bool = False,
) -> Callable[[str], str]:
    """Create one class-bound capability with policy-controlled identity reuse."""
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

    def configured_target() -> URIRef | None:
        """Return an exact config/SPARQL-bound extension target for this class."""
        matches = {
            item["target_iri"]
            for item in _enrichment_targets_from_global_state()
            if item["class_iri"] == class_iri
        }
        if len(matches) != 1:
            return None
        return URIRef(next(iter(matches)))

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
        bound_target = configured_target()
        if bound_target is not None:
            for type_ref in sorted(type_refs, key=str):
                graph.add((bound_target, RDF.type, type_ref))
            graph.set((bound_target, RDFS.label, Literal(normalized_label)))
            return str(bound_target)
        if reuse_by_label:
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
        iri = _short_random_iri()
        subject = URIRef(iri)
        for type_ref in sorted(type_refs, key=str):
            graph.add((subject, RDF.type, type_ref))
        graph.add((subject, RDFS.label, Literal(normalized_label)))
        return iri

    return create


def _compile_entity_capabilities(
    ontology_contract: dict[str, Any],
) -> dict[str, Callable[[str], str]]:
    """Compile class creators using the contract's explicit reuse policy."""
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
    reuse_policy = ontology_contract.get("reuse_policy") or {}
    reusable_class_iris = {
        str(item.get("class_iri") or "").strip()
        for item in reuse_policy.get("classes") or []
        if isinstance(item, dict)
        and item.get("reusable") is True
        and str(item.get("class_iri") or "").strip()
    }
    return {
        class_iri: _bound_entity_creator(
            class_iri,
            explicit_type_iris=closure.get(class_iri, {class_iri}),
            strict_subclass_iris=strict_subclasses.get(class_iri, set()),
            reuse_by_label=class_iri in reusable_class_iris,
        )
        for item in ontology_contract.get("classes") or []
        if (class_iri := str(item.get("class_iri") or "").strip())
    }


def package_entity_capabilities() -> dict[str, Callable[[str], str]]:
    """Load immutable class-creation capabilities shipped with this package."""
    contract_path = relationship_contract_path()
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
        ordering_property_iri = str(item.get("ordering_property_iri") or "").strip()
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


def package_ordered_entity_capabilities() -> dict[str, Callable[[str, int], str]]:
    """Load atomic ordered creators compiled from this package's T-Box contract."""
    contract_path = relationship_contract_path()
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
    contract_path = relationship_contract_path()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package ontology contract must be a JSON object")
    allowed_class_iris = _om2_quantity_range_iris(contract)
    om2_package = om2_runtime_package()
    try:
        om2_runtime = importlib.import_module(f"{om2_package}._fixed_om2_runtime")
    except ModuleNotFoundError:
        try:
            om2_runtime = importlib.import_module(f"{om2_package}.om2")
        except ModuleNotFoundError:
            om2_runtime = importlib.import_module(f"{om2_package}.fixed_om2_runtime")
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
            return URIRef(_short_random_iri())

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
    """Create a bounded OM-2 quantity from a compact label.

    Numeric labels use ``<number> <unit>``. The unit must be a compiled alias.
    """
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
        occurrence_local=True,
        message=(
            "Created a fresh occurrence-local OM-2 quantity. Never reuse this IRI "
            "for another relationship owner, even when the value and unit match."
        ),
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
                {
                    "predicate_iri": predicate_iri,
                    "subject_iri": str(subject),
                    "skippable": False,
                    "retryable": True,
                    "recovery": {
                        "action": "use_bound_root_or_candidate_subject",
                        "bound_root_iri": bound_root_iri(),
                        "candidate_subjects": _typed_subject_candidates(
                            graph,
                            domain_iris,
                            subclass_closure,
                        ),
                    },
                },
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
        if isinstance(value, str):
            value = sanitize_tool_text(value)
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


def package_datatype_capabilities() -> dict[str, Callable[[str, Any], dict[str, Any]]]:
    """Load immutable datatype capabilities shipped with this package."""
    contract_path = relationship_contract_path()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        raise ValueError("Package ontology contract must be a JSON object")
    return _compile_datatype_capabilities(contract)
