"""T-Box domain/range, OWL restrictions, ordering, and OM-2 quantity shape."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from src.extraction_prompt_generation.tbox.runtime_contracts import (
    derive_ordered_member_contracts,
)

from .graph_index import (
    XSD_INTEGER,
    compatible_type,
    expand_class_expression,
    quantity_class_iris,
    types_of,
    typed_instance_nodes,
)
from .report import Finding

OM2_HAS_NUMERICAL_VALUE = URIRef(
    "http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue"
)
OM2_HAS_UNIT = URIRef(
    "http://www.ontology-of-units-of-measure.org/resource/om-2/hasUnit"
)


def _literal_matches_range(value: Literal, range_iri: str) -> bool:
    declared = str(value.datatype or "").strip()
    if not range_iri:
        return True
    if range_iri in XSD_INTEGER:
        if declared and declared not in XSD_INTEGER:
            python_value = value.toPython()
            return isinstance(python_value, int) and not isinstance(python_value, bool)
        try:
            int(value.toPython())
            return True
        except (TypeError, ValueError):
            return False
    if range_iri in {str(XSD.double), str(XSD.float), str(XSD.decimal)}:
        try:
            float(value.toPython())
            return True
        except (TypeError, ValueError):
            return False
    if range_iri == str(XSD.boolean):
        return isinstance(value.toPython(), bool) or str(value).lower() in {
            "true",
            "false",
            "1",
            "0",
        }
    return True


def check_domain_range(
    abox: Graph,
    *,
    tbox: Graph,
    vocab: set[str],
    closure: dict[str, set[str]],
) -> list[Finding]:
    findings: list[Finding] = []
    instances = typed_instance_nodes(abox, vocab)
    object_props = set(tbox.subjects(RDF.type, OWL.ObjectProperty))
    datatype_props = set(tbox.subjects(RDF.type, OWL.DatatypeProperty))
    for subject, predicate, obj in abox:
        if not isinstance(subject, URIRef) or not isinstance(predicate, URIRef):
            continue
        if predicate in {RDF.type, RDFS.label, RDFS.comment}:
            continue
        if predicate not in object_props and predicate not in datatype_props:
            continue
        domains = []
        for domain in tbox.objects(predicate, RDFS.domain):
            domains.extend(expand_class_expression(tbox, domain))
        if domains and subject in instances:
            actual = types_of(subject, abox=abox, tbox=tbox, closure=closure)
            if not compatible_type(actual, (str(item) for item in domains), closure):
                findings.append(
                    Finding(
                        code="DOMAIN_VIOLATION",
                        check="tbox",
                        message=(
                            f"Subject of {predicate} is not typed as a declared domain "
                            f"({', '.join(str(item) for item in domains)})."
                        ),
                        iris=(str(subject), str(predicate)),
                    )
                )
        ranges = []
        for rng in tbox.objects(predicate, RDFS.range):
            ranges.extend(expand_class_expression(tbox, rng))
        if not ranges:
            continue
        if predicate in datatype_props:
            if not isinstance(obj, Literal):
                findings.append(
                    Finding(
                        code="RANGE_VIOLATION",
                        check="tbox",
                        message=f"Datatype property {predicate} has a non-literal object.",
                        iris=(str(subject), str(predicate)),
                    )
                )
            elif not _literal_matches_range(obj, str(ranges[0])):
                findings.append(
                    Finding(
                        code="RANGE_VIOLATION",
                        check="tbox",
                        message=f"Literal for {predicate} does not match range {ranges[0]}.",
                        iris=(str(subject), str(predicate)),
                    )
                )
            continue
        if not isinstance(obj, URIRef):
            findings.append(
                Finding(
                    code="RANGE_VIOLATION",
                    check="tbox",
                    message=f"Object property {predicate} has a literal object.",
                    iris=(str(subject), str(predicate)),
                )
            )
            continue
        actual_obj = types_of(obj, abox=abox, tbox=tbox, closure=closure)
        if not actual_obj:
            continue
        if not compatible_type(actual_obj, (str(item) for item in ranges), closure):
            findings.append(
                Finding(
                    code="RANGE_VIOLATION",
                    check="tbox",
                    message=(
                        f"Object of {predicate} is not typed as a declared range "
                        f"({', '.join(str(item) for item in ranges)})."
                    ),
                    iris=(str(subject), str(predicate), str(obj)),
                )
            )
    return findings


def check_owl_restrictions(
    abox: Graph,
    *,
    tbox: Graph,
    vocab: set[str],
    closure: dict[str, set[str]],
) -> list[Finding]:
    findings: list[Finding] = []
    instances = typed_instance_nodes(abox, vocab)
    for cls, _, restriction in tbox.triples((None, RDFS.subClassOf, None)):
        if not isinstance(cls, URIRef):
            continue
        if (restriction, RDF.type, OWL.Restriction) not in tbox:
            continue
        on_property = tbox.value(restriction, OWL.onProperty)
        if not isinstance(on_property, URIRef):
            continue
        on_class = tbox.value(restriction, OWL.onClass)
        some = tbox.value(restriction, OWL.someValuesFrom)
        all_values = tbox.value(restriction, OWL.allValuesFrom)
        min_q = tbox.value(restriction, OWL.minQualifiedCardinality)
        max_q = tbox.value(restriction, OWL.maxQualifiedCardinality)
        min_c = tbox.value(restriction, OWL.minCardinality)
        max_c = tbox.value(restriction, OWL.maxCardinality)
        exact = tbox.value(restriction, OWL.cardinality)
        class_iri = str(cls)
        members = [
            node
            for node in instances
            if class_iri in types_of(node, abox=abox, tbox=tbox, closure=closure)
        ]
        for node in members:
            objects = [
                obj
                for obj in abox.objects(node, on_property)
                if isinstance(obj, URIRef)
            ]
            if on_class is not None:
                expected = [str(item) for item in expand_class_expression(tbox, on_class)]
                objects = [
                    obj
                    for obj in objects
                    if compatible_type(
                        types_of(obj, abox=abox, tbox=tbox, closure=closure),
                        expected,
                        closure,
                    )
                ]
            count = len(objects)
            if min_q is not None or min_c is not None:
                try:
                    needed = int((min_q or min_c).toPython())
                except (TypeError, ValueError):
                    needed = 0
                if count < needed:
                    findings.append(
                        Finding(
                            code="OWL_RESTRICTION",
                            check="tbox",
                            message=(
                                f"{node} is a {class_iri} but has {count} values for "
                                f"{on_property} (min {needed})."
                            ),
                            iris=(str(node), str(on_property)),
                        )
                    )
            if max_q is not None or max_c is not None or exact is not None:
                try:
                    limit = int((max_q or max_c or exact).toPython())
                except (TypeError, ValueError):
                    limit = None
                if exact is not None and count != limit:
                    findings.append(
                        Finding(
                            code="OWL_RESTRICTION",
                            check="tbox",
                            message=(
                                f"{node} is a {class_iri} but has {count} values for "
                                f"{on_property} (exactly {limit})."
                            ),
                            iris=(str(node), str(on_property)),
                        )
                    )
                elif limit is not None and exact is None and count > limit:
                    findings.append(
                        Finding(
                            code="OWL_RESTRICTION",
                            check="tbox",
                            message=(
                                f"{node} is a {class_iri} but has {count} values for "
                                f"{on_property} (max {limit})."
                            ),
                            iris=(str(node), str(on_property)),
                        )
                    )
            filler = some or all_values
            if some is not None and count == 0:
                findings.append(
                    Finding(
                        code="OWL_RESTRICTION",
                        check="tbox",
                        message=f"{node} lacks a someValuesFrom filler on {on_property}.",
                        iris=(str(node), str(on_property)),
                    )
                )
            if all_values is not None:
                expected = [str(item) for item in expand_class_expression(tbox, filler)]
                for obj in abox.objects(node, on_property):
                    if not isinstance(obj, URIRef):
                        continue
                    actual = types_of(obj, abox=abox, tbox=tbox, closure=closure)
                    if actual and not compatible_type(actual, expected, closure):
                        findings.append(
                            Finding(
                                code="OWL_RESTRICTION",
                                check="tbox",
                                message=(
                                    f"{node} has an allValuesFrom violation on {on_property}."
                                ),
                                iris=(str(node), str(on_property), str(obj)),
                            )
                        )
    return findings


def check_disjoint_types(
    abox: Graph,
    *,
    tbox: Graph,
    vocab: set[str],
    closure: dict[str, set[str]],
) -> list[Finding]:
    findings: list[Finding] = []
    instances = typed_instance_nodes(abox, vocab)
    pairs: set[tuple[str, str]] = set()
    for left, _, right in tbox.triples((None, OWL.disjointWith, None)):
        if isinstance(left, URIRef) and isinstance(right, URIRef):
            ordered = tuple(sorted((str(left), str(right))))
            pairs.add((ordered[0], ordered[1]))
    for left_iri, right_iri in pairs:
        for node in instances:
            types = types_of(node, abox=abox, tbox=tbox, closure=closure)
            if left_iri in types and right_iri in types:
                findings.append(
                    Finding(
                        code="DISJOINT_TYPES",
                        check="tbox",
                        message=(
                            f"{node} is typed as disjoint classes {left_iri} and {right_iri}."
                        ),
                        iris=(str(node), left_iri, right_iri),
                    )
                )
    return findings


def check_functional_properties(
    abox: Graph,
    *,
    tbox: Graph,
    vocab: set[str],
) -> list[Finding]:
    findings: list[Finding] = []
    instances = typed_instance_nodes(abox, vocab)
    for prop in tbox.subjects(RDF.type, OWL.FunctionalProperty):
        if not isinstance(prop, URIRef):
            continue
        values: dict[URIRef, set[str]] = defaultdict(set)
        for subject, _, obj in abox.triples((None, prop, None)):
            if subject not in instances:
                continue
            values[subject].add(str(obj))
        for subject, objs in values.items():
            if len(objs) > 1:
                findings.append(
                    Finding(
                        code="FUNCTIONAL_PROPERTY",
                        check="tbox",
                        message=f"{subject} has {len(objs)} values for functional {prop}.",
                        iris=(str(subject), str(prop)),
                    )
                )
    for prop in tbox.subjects(RDF.type, OWL.InverseFunctionalProperty):
        if not isinstance(prop, URIRef):
            continue
        subjects: dict[str, set[str]] = defaultdict(set)
        for subject, _, obj in abox.triples((None, prop, None)):
            subjects[str(obj)].add(str(subject))
        for obj, subs in subjects.items():
            if len(subs) > 1:
                findings.append(
                    Finding(
                        code="INVERSE_FUNCTIONAL_PROPERTY",
                        check="tbox",
                        message=f"{obj} is the object of inverse-functional {prop} for {len(subs)} subjects.",
                        iris=(obj, str(prop)),
                    )
                )
    return findings


def check_ordered_members(
    abox: Graph,
    *,
    tbox_path: Path,
    vocab: set[str],
    closure: dict[str, set[str]],
    tbox: Graph,
) -> list[Finding]:
    findings: list[Finding] = []
    try:
        contracts = derive_ordered_member_contracts(tbox_path)
    except Exception:
        return findings
    instances = typed_instance_nodes(abox, vocab)
    for contract in contracts:
        collection = URIRef(contract["collection_property_iri"])
        member_class = str(contract["member_class_iri"])
        order_prop = URIRef(contract["order_property_iri"])
        for parent in instances:
            members = [
                obj
                for obj in abox.objects(parent, collection)
                if isinstance(obj, URIRef)
                and member_class
                in types_of(obj, abox=abox, tbox=tbox, closure=closure)
            ]
            seen_orders: dict[int, list[str]] = defaultdict(list)
            for member in members:
                values = list(abox.objects(member, order_prop))
                if len(values) != 1:
                    findings.append(
                        Finding(
                            code="ORDER_INVALID",
                            check="tbox",
                            message=(
                                f"Ordered member {member} must have exactly one integer order."
                            ),
                            iris=(str(parent), str(member)),
                        )
                    )
                    continue
                try:
                    order = int(values[0].toPython())
                except (TypeError, ValueError):
                    findings.append(
                        Finding(
                            code="ORDER_INVALID",
                            check="tbox",
                            message=f"Ordered member {member} has a non-integer order.",
                            iris=(str(parent), str(member)),
                        )
                    )
                    continue
                seen_orders[order].append(str(member))
            for order, nodes in seen_orders.items():
                if len(nodes) > 1:
                    findings.append(
                        Finding(
                            code="ORDER_DUPLICATE",
                            check="tbox",
                            message=(
                                f"{parent} has {len(nodes)} ordered members at order {order}."
                            ),
                            iris=(str(parent), *nodes),
                        )
                    )
    return findings


def check_om2_quantities(
    abox: Graph,
    *,
    tbox: Graph,
    vocab: set[str],
    closure: dict[str, set[str]],
) -> list[Finding]:
    findings: list[Finding] = []
    quantity_classes = quantity_class_iris(tbox)
    if not quantity_classes:
        return findings
    instances = typed_instance_nodes(abox, vocab)
    for node in instances:
        types = types_of(node, abox=abox, tbox=tbox, closure=closure)
        if not (types & quantity_classes):
            continue
        numbers = list(abox.objects(node, OM2_HAS_NUMERICAL_VALUE))
        units = [
            obj
            for obj in abox.objects(node, OM2_HAS_UNIT)
            if isinstance(obj, URIRef)
        ]
        labels = list(abox.objects(node, RDFS.label))
        if not numbers and not labels:
            findings.append(
                Finding(
                    code="OM2_QUANTITY_INCOMPLETE",
                    check="tbox",
                    message=(
                        f"{node} is an OM-2 quantity without a numerical value or label."
                    ),
                    iris=(str(node),),
                )
            )
        if numbers and not units:
            findings.append(
                Finding(
                    code="OM2_QUANTITY_INCOMPLETE",
                    check="tbox",
                    message=f"{node} has om-2:hasNumericalValue but no om-2:hasUnit.",
                    iris=(str(node),),
                )
            )
    return findings
