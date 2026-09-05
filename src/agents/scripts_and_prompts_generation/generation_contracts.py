"""Generation contracts and validators for ontology-driven MCP artifacts.

The functions in this module deliberately derive operational constraints from
the ontology T-Box and runtime configuration. They should not contain benchmark
or paper-specific facts.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from rdflib import Graph, OWL, RDF, RDFS, URIRef  # type: ignore[import-not-found]

from src.agents.scripts_and_prompts_generation.ttl_parser import (
    extract_ontology_integrity_profile,
)


def build_validation_observation(
    *,
    check_id: str,
    subject_key: str,
    stage: str,
    failures: list[str] | None = None,
    warnings: list[str] | None = None,
    observed_artifacts: list[str] | None = None,
    blocked_by: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Build a stable, machine-routable fact emitted by a generation validator."""
    failure_items = [str(item) for item in (failures or [])]
    warning_items = [str(item) for item in (warnings or [])]
    blockers = [str(item) for item in (blocked_by or [])]
    if blockers:
        status = "blocked"
    elif failure_items:
        status = "fail"
    else:
        status = "pass"
    observation_evidence = dict(evidence or {})
    if failure_items:
        observation_evidence.setdefault("failures", failure_items)
    if warning_items:
        observation_evidence.setdefault("warnings", warning_items)
    return {
        "check_id": str(check_id),
        "subject_key": str(subject_key),
        "stage": str(stage),
        "status": status,
        "observed_artifacts": [
            str(artifact) for artifact in (observed_artifacts or [])
        ],
        "blocked_by": blockers,
        "evidence": observation_evidence,
        "message": message
        or (
            f"{check_id} found {len(failure_items)} failure(s)"
            if failure_items
            else f"{check_id} completed with {len(warning_items)} warning(s)"
            if warning_items
            else f"{check_id} passed"
        ),
    }


def _local_name(iri: Any) -> str:
    text = str(iri or "").strip()
    if not text:
        return ""
    return text.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _external_creator_specs(
    external_class_iris: set[str],
    *,
    internal_class_locals: set[str],
) -> list[dict[str, str]]:
    """Assign stable, collision-safe tools to contract-referenced external classes."""
    grouped: dict[str, list[str]] = {}
    for class_iri in sorted(external_class_iris):
        local = _local_name(class_iri)
        if local:
            grouped.setdefault(local, []).append(class_iri)
    specs: list[dict[str, str]] = []
    for local, class_iris in sorted(grouped.items()):
        needs_suffix = local in internal_class_locals or len(class_iris) > 1
        for class_iri in class_iris:
            suffix = hashlib.sha256(class_iri.encode("utf-8")).hexdigest()[:8]
            tool_local = f"{local}_{suffix}" if needs_suffix else local
            specs.append(
                {
                    "class_iri": class_iri,
                    "class_local": local,
                    "tool_name": f"create_{tool_local}",
                    "check_tool_name": f"check_existing_{tool_local}",
                    "source": "object_property_external_range",
                }
            )
    return specs


def _namespace_iri(iri: str) -> str:
    text = str(iri or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[0] + "#"
    if "/" in text:
        return text.rsplit("/", 1)[0] + "/"
    return ""


def _iter_rdf_list(graph: Graph, node: Any) -> list[Any]:
    out: list[Any] = []
    current = node
    while current and current != RDF.nil:
        first = graph.value(current, RDF.first)
        if first is not None:
            out.append(first)
        current = graph.value(current, RDF.rest)
    return out


def _domain_members(graph: Graph, domain: Any) -> list[str]:
    if isinstance(domain, URIRef):
        return [str(domain)]
    members: list[str] = []
    for union_list in graph.objects(domain, OWL.unionOf):
        members.extend(str(x) for x in _iter_rdf_list(graph, union_list) if isinstance(x, URIRef))
    return members


def _subclass_closure(graph: Graph) -> dict[str, set[str]]:
    classes = {
        str(c)
        for class_type in (OWL.Class, RDFS.Class)
        for c in graph.subjects(RDF.type, class_type)
        if isinstance(c, URIRef)
    }
    for child, parent in graph.subject_objects(RDFS.subClassOf):
        if isinstance(child, URIRef):
            classes.add(str(child))
        if isinstance(parent, URIRef):
            classes.add(str(parent))
    for predicate in (RDFS.domain, RDFS.range):
        classes.update(
            str(value)
            for value in graph.objects(None, predicate)
            if isinstance(value, URIRef)
        )
    closure: dict[str, set[str]] = {c: {c} for c in classes}
    changed = True
    while changed:
        changed = False
        for child in list(closure):
            node = URIRef(child)
            for parent in graph.objects(node, RDFS.subClassOf):
                if not isinstance(parent, URIRef):
                    continue
                before = len(closure[child])
                closure[child].add(str(parent))
                closure[child].update(closure.get(str(parent), {str(parent)}))
                changed = changed or len(closure[child]) != before
    return closure


def _choose_union_superclass(members: list[str], closure: dict[str, set[str]]) -> str:
    for candidate in members:
        if all(candidate in closure.get(member, {member}) for member in members):
            return candidate
    return ""


def load_meta_task_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _ontology_config(
    meta_cfg: dict[str, Any], ontology_name: str | None
) -> dict[str, Any]:
    ontologies = meta_cfg.get("ontologies", {}) or {}
    candidates = [ontologies.get("main", {})] + list(
        ontologies.get("extensions", []) or []
    )
    if ontology_name:
        return next(
            (
                candidate
                for candidate in candidates
                if isinstance(candidate, dict)
                and str(candidate.get("name") or "") == ontology_name
            ),
            {},
        )
    main = ontologies.get("main", {}) or {}
    return main if isinstance(main, dict) else {}


def _resolve_tbox_path(
    ttl_file: str, meta_task_config_path: str | Path
) -> Path:
    configured = Path(ttl_file)
    candidates = [configured]
    if not configured.is_absolute():
        config_path = Path(meta_task_config_path).resolve()
        candidates.extend(
            [
                config_path.parent / configured,
                config_path.parent.parent.parent / configured,
            ]
        )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"Configured ontology T-Box not found: {ttl_file}")


def _restriction_nodes(graph: Graph, class_node: URIRef) -> list[Any]:
    nodes: list[Any] = []
    for predicate in (RDFS.subClassOf, OWL.equivalentClass):
        for candidate in graph.objects(class_node, predicate):
            if (candidate, RDF.type, OWL.Restriction) in graph:
                nodes.append(candidate)
            for member_list in graph.objects(candidate, OWL.intersectionOf):
                nodes.extend(
                    member
                    for member in _iter_rdf_list(graph, member_list)
                    if (member, RDF.type, OWL.Restriction) in graph
                )
    return nodes


def _literal_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


_TOP_ROLE_PREDICATE_LOCALS = {
    "topEntityClass",
    "topEntityRole",
    "publishTopEntityClass",
}


def _machine_top_role(graph: Graph, *, evidence_file: str) -> dict[str, Any]:
    """Read an explicit top-role declaration without inferring one from schema shape."""
    declarations: set[str] = set()
    evidence: list[dict[str, str]] = []
    for subject, predicate, obj in graph:
        if _local_name(predicate) not in _TOP_ROLE_PREDICATE_LOCALS:
            continue
        candidate = ""
        if isinstance(obj, URIRef):
            candidate = str(obj)
        elif isinstance(subject, URIRef) and str(obj).strip().lower() in {"true", "1"}:
            candidate = str(subject)
        if not candidate:
            continue
        declarations.add(candidate)
        evidence.append(
            {
                "ttl_file": evidence_file,
                "subject_iri": str(subject),
                "predicate_iri": str(predicate),
                "object": str(obj),
            }
        )
    if len(declarations) == 1:
        class_iri = next(iter(declarations))
        return {
            "status": "known",
            "class_iri": class_iri,
            "class_local": _local_name(class_iri),
            "source": "tbox",
            "evidence": evidence,
        }
    return {
        "status": "ambiguous" if declarations else "unknown",
        "class_iri": "",
        "class_local": "",
        "source": "tbox",
        "evidence": evidence,
    }


def build_ontology_publish_contract_from_tbox(
    tbox_path: str | Path,
    *,
    ontology_name: str = "",
    configured_ttl_file: str = "",
) -> dict[str, Any]:
    """Build the semantic publish contract directly from one ontology T-Box."""
    resolved_path = Path(tbox_path).resolve()
    graph = Graph()
    graph.parse(str(resolved_path), format="turtle")
    closure = _subclass_closure(graph)
    evidence_file = str(resolved_path)

    classes = [
        {
            "class_iri": class_iri,
            "source": "tbox",
            "evidence": {
                "ttl_file": evidence_file,
                "triple_pattern": "rdf:type owl:Class/rdfs:Class or referenced class",
            },
        }
        for class_iri in sorted(closure)
    ]
    subclass_closure = [
        {
            "class_iri": class_iri,
            "superclass_iris": sorted(superclasses),
            "source": "tbox",
            "evidence": {
                "ttl_file": evidence_file,
                "predicate_iri": str(RDFS.subClassOf),
            },
        }
        for class_iri, superclasses in sorted(closure.items())
    ]

    object_properties: list[dict[str, Any]] = []
    datatype_properties: list[dict[str, Any]] = []
    structured_constraints: list[dict[str, Any]] = []
    required_links: list[dict[str, Any]] = []
    for prop in sorted(
        {
            node
            for node in graph.subjects(RDF.type, OWL.ObjectProperty)
            if isinstance(node, URIRef)
        },
        key=str,
    ):
        domains: list[str] = []
        for domain in graph.objects(prop, RDFS.domain):
            domains.extend(_domain_members(graph, domain))
        ranges = sorted(
            str(value)
            for value in graph.objects(prop, RDFS.range)
            if isinstance(value, URIRef)
        )
        object_properties.append(
            {
                "property_iri": str(prop),
                "domain_iris": sorted(set(domains)),
                "range_iris": ranges,
                "source": "tbox",
                "evidence": {
                    "ttl_file": evidence_file,
                    "property_type": str(OWL.ObjectProperty),
                    "domain_predicate": str(RDFS.domain),
                    "range_predicate": str(RDFS.range),
                },
            }
        )
    for prop in sorted(
        {
            node
            for node in graph.subjects(RDF.type, OWL.DatatypeProperty)
            if isinstance(node, URIRef)
        },
        key=str,
    ):
        domains: list[str] = []
        for domain in graph.objects(prop, RDFS.domain):
            domains.extend(_domain_members(graph, domain))
        datatype_properties.append(
            {
                "property_iri": str(prop),
                "domain_iris": sorted(set(domains)),
                "range_iris": sorted(
                    str(value)
                    for value in graph.objects(prop, RDFS.range)
                    if isinstance(value, URIRef)
                ),
                "source": "tbox",
                "evidence": {
                    "ttl_file": evidence_file,
                    "property_type": str(OWL.DatatypeProperty),
                    "domain_predicate": str(RDFS.domain),
                    "range_predicate": str(RDFS.range),
                },
            }
        )

    object_property_iris = {item["property_iri"] for item in object_properties}
    for class_iri in sorted(closure):
        class_node = URIRef(class_iri)
        for restriction in _restriction_nodes(graph, class_node):
            prop = graph.value(restriction, OWL.onProperty)
            if not isinstance(prop, URIRef) or str(prop) not in object_property_iris:
                continue
            cardinalities = (
                (OWL.minCardinality, "min_cardinality"),
                (OWL.cardinality, "cardinality"),
                (OWL.minQualifiedCardinality, "min_qualified_cardinality"),
                (OWL.qualifiedCardinality, "qualified_cardinality"),
            )
            for cardinality_predicate, kind in cardinalities:
                count = _literal_int(graph.value(restriction, cardinality_predicate))
                if count is None:
                    continue
                target = graph.value(restriction, OWL.onClass)
                if not isinstance(target, URIRef):
                    target = graph.value(prop, RDFS.range)
                restriction_fingerprint = hashlib.sha256(
                    json.dumps(
                        {
                            "subject_class_iri": class_iri,
                            "predicate_iri": str(prop),
                            "target_class_iri": (
                                str(target) if isinstance(target, URIRef) else ""
                            ),
                            "cardinality_predicate_iri": str(
                                cardinality_predicate
                            ),
                            "count": count,
                            "constraint_kind": kind,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()[:16]
                item = {
                    "subject_class_iri": class_iri,
                    "predicate_iri": str(prop),
                    "target_class_iri": str(target) if isinstance(target, URIRef) else "",
                    "min_count": count,
                    "constraint_kind": kind,
                    "source": "owl_restriction",
                    "evidence": {
                        "ttl_file": evidence_file,
                        "restriction_node": (
                            f"_:deterministic-restriction-{restriction_fingerprint}"
                        ),
                        "cardinality_predicate_iri": str(cardinality_predicate),
                    },
                }
                structured_constraints.append(item)
                if count > 0:
                    required_links.append(item)

    return {
        "ontology_name": ontology_name,
        "ttl_file": configured_ttl_file or str(tbox_path),
        "resolved_ttl_file": evidence_file,
        "top_role": _machine_top_role(graph, evidence_file=evidence_file),
        "classes": classes,
        "subclass_closure": subclass_closure,
        "object_properties": object_properties,
        "datatype_properties": datatype_properties,
        "constraints": structured_constraints,
        "required_links": required_links,
    }


def build_ontology_publish_contract(
    *,
    meta_task_config_path: str | Path = "configs/meta_task/meta_task_config.json",
    ontology_name: str | None = None,
) -> dict[str, Any]:
    """Build a publish contract solely from the configured ontology T-Box."""
    meta_cfg = load_meta_task_config(meta_task_config_path)
    ontology_cfg = _ontology_config(meta_cfg, ontology_name)
    ttl_file = str(ontology_cfg.get("ttl_file") or "").strip()
    if not ttl_file:
        raise FileNotFoundError("Ontology config does not define ttl_file")
    tbox_path = _resolve_tbox_path(ttl_file, meta_task_config_path)

    return build_ontology_publish_contract_from_tbox(
        tbox_path,
        ontology_name=str(ontology_cfg.get("name") or ontology_name or ""),
        configured_ttl_file=ttl_file,
    )


def build_generation_contract_bundle(
    *,
    meta_task_config_path: str | Path = "configs/meta_task/meta_task_config.json",
    ontology_name: str | None = None,
) -> dict[str, Any]:
    """Build a machine-readable contract bundle from T-Box and runtime policies."""
    meta_cfg = load_meta_task_config(meta_task_config_path)
    main = _ontology_config(meta_cfg, ontology_name)

    onto_name = str(main.get("name") or ontology_name or "").strip()
    ttl_file = str(main.get("ttl_file") or "").strip()
    graph = Graph()
    resolved_ttl_file = ""
    if ttl_file:
        try:
            resolved_ttl_file = str(
                _resolve_tbox_path(ttl_file, meta_task_config_path)
            )
            graph.parse(resolved_ttl_file, format="turtle")
        except FileNotFoundError:
            resolved_ttl_file = ""

    closure = _subclass_closure(graph)
    relationship_domain_contracts: dict[str, dict[str, Any]] = {}
    relationship_tool_contracts: dict[str, dict[str, Any]] = {}
    quantity_properties: list[dict[str, str]] = []
    ordered_profile = (
        extract_ontology_integrity_profile(resolved_ttl_file)
        if resolved_ttl_file
        else {}
    )
    runtime_policies = main.get("runtime_policies") or {}
    main_entity_policy = runtime_policies.get("main_entity_kg") or {}
    publish_policy = main_entity_policy.get("publish") or {}
    reconciliation_policy = publish_policy.get("hint_reconciliation") or {}
    shell_policy = main_entity_policy.get("shell_validation") or {}

    configured_order_properties = {
        _local_name(str(item.get("order_predicate_iri") or ""))
        for item in reconciliation_policy.get("ordered_member_hint_contracts") or []
        if isinstance(item, dict)
        and str(item.get("order_predicate_iri") or "").strip()
    }
    configured_member_classes: set[str] = set()
    configured_collection_properties: set[str] = set()
    for item in runtime_policies.get("ordered_member_contracts") or []:
        if not isinstance(item, dict):
            continue
        target_iri = str(item.get("member_class_iri") or "").strip()
        collection_iri = str(item.get("collection_property_iri") or "").strip()
        order_iri = str(item.get("order_property_iri") or "").strip()
        if collection_iri:
            configured_collection_properties.add(_local_name(collection_iri))
        if order_iri:
            configured_order_properties.add(_local_name(order_iri))
        if target_iri:
            configured_member_classes.update(
                _local_name(class_iri)
                for class_iri, superclass_iris in closure.items()
                if target_iri in superclass_iris
            )
    for item in shell_policy.get("required_links") or []:
        if not isinstance(item, dict) or item.get("ordered_member") is not True:
            continue
        target_iri = str(item.get("target_class_iri") or "").strip()
        predicate_iri = str(item.get("predicate_iri") or "").strip()
        if predicate_iri:
            configured_collection_properties.add(_local_name(predicate_iri))
        if target_iri:
            configured_member_classes.update(
                _local_name(class_iri)
                for class_iri, superclass_iris in closure.items()
                if target_iri in superclass_iris
            )

    ordered_profile["ordered_member_classes"] = sorted(
        set(ordered_profile.get("ordered_member_classes") or [])
        | configured_member_classes
    )
    ordered_profile["individually_linked_object_properties"] = sorted(
        set(ordered_profile.get("individually_linked_object_properties") or [])
        | configured_collection_properties
    )
    ordered_profile["single_valued_ordering_properties"] = sorted(
        set(ordered_profile.get("single_valued_ordering_properties") or [])
        | configured_order_properties
    )
    ordered_member_locals = {
        str(x).strip()
        for x in (ordered_profile.get("ordered_member_classes") or [])
        if str(x).strip()
    }
    step_scoped_object_properties: list[dict[str, str]] = []
    required_step_scoped_object_properties: list[dict[str, str]] = []
    namespace_candidates = sorted({_namespace_iri(str(s)) for s in graph.subjects() if isinstance(s, URIRef)})
    namespace = next((x for x in namespace_candidates if onto_name.lower() in x.lower()), "")
    ontology_symbol_locals = sorted(
        {
            _local_name(node)
            for node in set(graph.subjects()) | set(graph.predicates()) | set(graph.objects())
            if isinstance(node, URIRef)
        }
    )
    declared_class_iris = {
        str(node)
        for class_type in (OWL.Class, RDFS.Class)
        for node in graph.subjects(RDF.type, class_type)
        if isinstance(node, URIRef)
    }
    internal_class_locals = {
        _local_name(class_iri)
        for class_iri in declared_class_iris
        if namespace and class_iri.startswith(namespace)
    }
    external_entity_range_iris: set[str] = set()
    for prop in graph.subjects(RDF.type, OWL.ObjectProperty):
        if not isinstance(prop, URIRef):
            continue
        prop_iri = str(prop)
        ranges = [str(r) for r in graph.objects(prop, RDFS.range) if isinstance(r, URIRef)]
        domains: list[str] = []
        union_domains: list[list[str]] = []
        for domain in graph.objects(prop, RDFS.domain):
            members = _domain_members(graph, domain)
            if members:
                domains.extend(members)
            if len(members) > 1:
                union_domains.append(members)
                preferred = _choose_union_superclass(members, closure)
                if preferred:
                    relationship_domain_contracts[_local_name(prop_iri)] = {
                        "predicate_iri": prop_iri,
                        "union_members": members,
                        "preferred_domain_iri": preferred,
                        "preferred_domain_local": _local_name(preferred),
                    }
        internal_range_iris = sorted(set(ranges) & declared_class_iris)
        external_range_iris = sorted(set(ranges) - declared_class_iris)
        internal_targets = sorted({_local_name(iri) for iri in internal_range_iris})
        most_specific_targets = ordered_profile.get(
            "most_specific_subclass_targets"
        ) or {}
        materialization_target_locals = sorted(
            {
                concrete
                for target in internal_targets
                for concrete in (
                    most_specific_targets.get(target) or [target]
                )
                if str(concrete).strip()
            }
        )
        external_targets = sorted({_local_name(iri) for iri in external_range_iris})
        om2_range_iris = sorted(
            iri
            for iri in external_range_iris
            if "ontology-of-units-of-measure.org/resource/om-2/" in iri
        )
        creatable_external_range_iris = sorted(
            set(external_range_iris) - set(om2_range_iris)
        )
        external_entity_range_iris.update(creatable_external_range_iris)
        external_creator_specs = _external_creator_specs(
            set(creatable_external_range_iris),
            internal_class_locals=internal_class_locals,
        )
        creator_tools = [
            f"create_{local}" for local in materialization_target_locals
        ]
        if om2_range_iris:
            creator_tools.append("create_om2_quantity")
        creator_tools.extend(
            spec["tool_name"] for spec in external_creator_specs
        )
        if internal_targets and external_targets:
            target_handling = "mixed"
        elif internal_targets:
            target_handling = "generated_creator"
        elif om2_range_iris:
            target_handling = "fixed_runtime_creator"
        elif creatable_external_range_iris:
            target_handling = "generated_external_creator"
        else:
            target_handling = "untyped_existing_iri"
        relationship_tool_contracts[_local_name(prop_iri)] = {
            "predicate_iri": prop_iri,
            "predicate_local": _local_name(prop_iri),
            "domain_iris": sorted(set(domains)),
            "range_iris": sorted(set(ranges)),
            "range_locals": sorted({_local_name(iri) for iri in ranges}),
            "internal_range_iris": internal_range_iris,
            "external_range_iris": external_range_iris,
            "internal_targets": internal_targets,
            "materialization_target_locals": materialization_target_locals,
            "external_targets": external_targets,
            "external_creator_specs": external_creator_specs,
            "fixed_runtime_range_iris": om2_range_iris,
            "creator_tools": creator_tools,
            "creator_available": bool(creator_tools),
            "target_handling": target_handling,
        }
        if any("ontology-of-units-of-measure.org" in r for r in ranges):
            quantity_properties.append(
                {
                    "predicate_iri": prop_iri,
                    "predicate_local": _local_name(prop_iri),
                    "domain_locals": ", ".join(sorted({_local_name(x) for x in domains if x})),
                    "range_iris": ", ".join(ranges),
                }
            )
        for domain_iri in domains:
            domain_local = _local_name(domain_iri)
            if domain_local not in ordered_member_locals:
                continue
            for range_iri in ranges:
                if "ontology-of-units-of-measure.org" in range_iri:
                    continue
                step_scoped_object_properties.append(
                    {
                        "predicate_iri": prop_iri,
                        "predicate_local": _local_name(prop_iri),
                        "domain_iri": domain_iri,
                        "domain_local": domain_local,
                        "range_iri": range_iri,
                        "range_local": _local_name(range_iri),
                    }
                )
    for restriction in graph.subjects(RDF.type, OWL.Restriction):
        for target_predicate in (
            OWL.onClass,
            OWL.someValuesFrom,
            OWL.allValuesFrom,
        ):
            target = graph.value(restriction, target_predicate)
            target_iri = str(target) if isinstance(target, URIRef) else ""
            if (
                target_iri
                and target_iri not in declared_class_iris
                and "ontology-of-units-of-measure.org/resource/om-2/"
                not in target_iri
            ):
                external_entity_range_iris.add(target_iri)
    external_class_creators = _external_creator_specs(
        external_entity_range_iris,
        internal_class_locals=internal_class_locals,
    )
    publish_contract = (
        build_ontology_publish_contract(
            meta_task_config_path=meta_task_config_path,
            ontology_name=ontology_name,
        )
        if resolved_ttl_file
        else {
            "classes": [],
            "subclass_closure": [],
            "object_properties": [],
            "constraints": [],
            "required_links": [],
            "top_role": {
                "status": "unknown",
                "class_iri": "",
                "class_local": "",
                "source": "tbox",
                "evidence": [],
            },
        }
    )
    top_role = dict(publish_contract["top_role"])

    return {
        "ontology_name": onto_name,
        "ttl_file": ttl_file,
        "namespace_uri": namespace,
        "contract_layers": {
            "tbox_derived": {
                "source": "active_tbox",
                "keys": [
                    "top_entity",
                    "required_links",
                    "ontology_publish_contract",
                    "ordered_member_profile",
                    "relationship_domain_contracts",
                    "relationship_tool_contracts",
                    "external_class_creators",
                    "step_scoped_object_properties",
                    "required_step_scoped_object_properties",
                    "om2_quantity_properties",
                    "ontology_symbol_locals",
                ],
            },
            "generation_runtime": {
                "source": "generic_infrastructure_policy",
                "lifecycle": "idempotent_open_or_resume",
                "default_export": "abox_only",
                "closed_world_surface": True,
            },
            "pipeline_only": {
                "source": "meta_task_config",
                "key": "runtime_policies",
                "may_enter_generation_prompt": False,
                "storage": "AgenticGenerationContext.pipeline_runtime_policies",
            },
        },
        "top_entity": {
            **top_role,
            "iter1_allows_multiple": True,
            "main_pass_reuses_scoped_root": False,
        },
        "required_links": publish_contract["required_links"],
        "ontology_publish_contract": publish_contract,
        "ordered_member_profile": ordered_profile,
        "relationship_domain_contracts": relationship_domain_contracts,
        "relationship_tool_contracts": relationship_tool_contracts,
        "external_class_creators": external_class_creators,
        "step_scoped_object_properties": sorted(
            step_scoped_object_properties,
            key=lambda x: (x["domain_local"], x["predicate_local"], x["range_local"]),
        ),
        "required_step_scoped_object_properties": sorted(
            required_step_scoped_object_properties,
            key=lambda x: (x["domain_local"], x["predicate_local"], x["range_local"]),
        ),
        "om2_quantity_properties": quantity_properties,
        "ontology_symbol_locals": ontology_symbol_locals,
    }


def build_relationship_tool_contracts_from_tbox(
    tbox_path: str | Path,
) -> dict[str, dict[str, Any]]:
    """Compile per-property tool metadata solely from a machine-readable T-Box."""
    graph = Graph()
    graph.parse(str(tbox_path), format="turtle")
    integrity_profile = extract_ontology_integrity_profile(str(tbox_path))
    most_specific_targets = (
        integrity_profile.get("most_specific_subclass_targets") or {}
    )
    declared_class_iris = {
        str(node)
        for class_type in (OWL.Class, RDFS.Class)
        for node in graph.subjects(RDF.type, class_type)
        if isinstance(node, URIRef)
    }
    property_namespaces = sorted(
        {
            _namespace_iri(str(node))
            for node in graph.subjects(RDF.type, OWL.ObjectProperty)
            if isinstance(node, URIRef)
        }
    )
    primary_namespace = property_namespaces[0] if len(property_namespaces) == 1 else ""
    internal_class_locals = {
        _local_name(class_iri)
        for class_iri in declared_class_iris
        if primary_namespace and class_iri.startswith(primary_namespace)
    }
    contracts: dict[str, dict[str, Any]] = {}
    for prop in graph.subjects(RDF.type, OWL.ObjectProperty):
        if not isinstance(prop, URIRef):
            continue
        prop_iri = str(prop)
        ranges = sorted(
            {
                str(value)
                for value in graph.objects(prop, RDFS.range)
                if isinstance(value, URIRef)
            }
        )
        domains: set[str] = set()
        for domain in graph.objects(prop, RDFS.domain):
            domains.update(_domain_members(graph, domain))
        internal_range_iris = sorted(set(ranges) & declared_class_iris)
        external_range_iris = sorted(set(ranges) - declared_class_iris)
        internal_targets = sorted({_local_name(iri) for iri in internal_range_iris})
        materialization_target_locals = sorted(
            {
                concrete
                for target in internal_targets
                for concrete in (most_specific_targets.get(target) or [target])
                if str(concrete).strip()
            }
        )
        external_targets = sorted({_local_name(iri) for iri in external_range_iris})
        om2_range_iris = sorted(
            iri
            for iri in external_range_iris
            if "ontology-of-units-of-measure.org/resource/om-2/" in iri
        )
        creatable_external_range_iris = sorted(
            set(external_range_iris) - set(om2_range_iris)
        )
        external_creator_specs = _external_creator_specs(
            set(creatable_external_range_iris),
            internal_class_locals=internal_class_locals,
        )
        creator_tools = [
            f"create_{local}" for local in materialization_target_locals
        ]
        if om2_range_iris:
            creator_tools.append("create_om2_quantity")
        creator_tools.extend(spec["tool_name"] for spec in external_creator_specs)
        if internal_targets and external_targets:
            target_handling = "mixed"
        elif internal_targets:
            target_handling = "generated_creator"
        elif om2_range_iris:
            target_handling = "fixed_runtime_creator"
        elif creatable_external_range_iris:
            target_handling = "generated_external_creator"
        else:
            target_handling = "untyped_existing_iri"
        contracts[_local_name(prop_iri)] = {
            "predicate_iri": prop_iri,
            "predicate_local": _local_name(prop_iri),
            "domain_iris": sorted(domains),
            "range_iris": ranges,
            "range_locals": sorted({_local_name(iri) for iri in ranges}),
            "internal_range_iris": internal_range_iris,
            "external_range_iris": external_range_iris,
            "internal_targets": internal_targets,
            "materialization_target_locals": materialization_target_locals,
            "external_targets": external_targets,
            "external_creator_specs": external_creator_specs,
            "fixed_runtime_range_iris": om2_range_iris,
            "creator_tools": creator_tools,
            "creator_available": bool(creator_tools),
            "target_handling": target_handling,
        }
    return contracts


def write_generation_contract_bundle(bundle: dict[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")


def _function_source(code: str, name: str) -> str:
    try:
        mod = ast.parse(code)
    except SyntaxError:
        return ""
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(code, node) or ""
    return ""


def _lower_initial(name: str) -> str:
    return name[:1].lower() + name[1:] if name else ""


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", str(name or "")).lower()


def _effective_step_scoped_props(contract_bundle: dict[str, Any]) -> list[dict[str, str]]:
    """Expand union-domain step properties into concrete T-Box class contracts."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_spec(prop_local: str, domain_local: str, range_local: str) -> None:
        if not (prop_local and domain_local and range_local):
            return
        key = (prop_local, domain_local, range_local)
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "predicate_local": prop_local,
                "domain_local": domain_local,
                "range_local": range_local,
            }
        )

    for spec in contract_bundle.get("step_scoped_object_properties") or []:
        add_spec(
            str((spec or {}).get("predicate_local") or "").strip(),
            str((spec or {}).get("domain_local") or "").strip(),
            str((spec or {}).get("range_local") or "").strip(),
        )

    property_constraints = ((contract_bundle.get("ordered_member_profile") or {}).get("property_constraints") or {})
    for prop_local, spec in (contract_bundle.get("relationship_domain_contracts") or {}).items():
        range_local = str(((property_constraints.get(prop_local) or {}).get("range") or "")).strip()
        for member in (spec or {}).get("union_members") or []:
            add_spec(str(prop_local or "").strip(), _local_name(member), range_local)
    return out


def validate_generated_artifacts(
    *,
    scripts_dir: str | Path,
    prompts_dir: str | Path | None = None,
    contract_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Validate generated artifacts against the contract bundle."""
    scripts = Path(scripts_dir)
    prompts = Path(prompts_dir) if prompts_dir else None
    failures: list[str] = []
    warnings: list[str] = []

    for path in sorted(scripts.glob("*.py")):
        if path.name.startswith("main_part_") or "_attempt_" in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        try:
            mod = ast.parse(text)
        except SyntaxError as e:
            failures.append(f"{path.name}: syntax error line {e.lineno}: {e.msg}")
            mod = None
        if mod is not None:
            available: set[str] = set()
            for node in mod.body:
                if isinstance(node, ast.FunctionDef):
                    available.add(node.name)
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        available.add(alias.asname or alias.name)
            # Private helper names and internal call signatures are intentionally
            # unconstrained. Import and runtime probes validate executable behavior.
        allowed_symbol_locals = {
            str(x).strip()
            for x in (contract_bundle.get("ontology_symbol_locals") or [])
            if str(x).strip()
        }
        has_allowed_1_2_symbol = any("1_2" in local and local in text for local in allowed_symbol_locals)
        if "chemica1" in text or ("1_2" in text and not has_allowed_1_2_symbol):
            failures.append(f"{path.name}: contains OCR/LLM-mangled public symbol text")
    if contract_bundle.get("om2_quantity_properties"):
        runtime_path = scripts / "_fixed_om2_runtime.py"
        if not runtime_path.is_file():
            failures.append("Missing fixed OM-2 runtime `_fixed_om2_runtime.py`")

    # Prompt wording is not a generated-code contract. End-to-end extraction and
    # materialization scoring owns whether prompts preserve required semantics.

    subject_key = str(contract_bundle.get("ontology_name") or scripts.name)
    observed_artifacts = [
        str(scripts),
        *([str(prompts)] if prompts is not None else []),
    ]
    observations = [
        build_validation_observation(
            check_id="generation.contract_bundle",
            subject_key=subject_key,
            stage="contract",
            failures=failures,
            warnings=warnings,
            observed_artifacts=observed_artifacts,
            evidence={"contract_ontology": contract_bundle.get("ontology_name")},
        )
    ]
    return {
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "observations": observations,
    }
