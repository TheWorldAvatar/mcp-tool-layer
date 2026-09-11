"""Entity-layer stage probes: creator surface, OM-2, range labels, source shape.

Runs against `_creation_entities.py`. See stage/README.md.
"""

from __future__ import annotations

import inspect
import json

from rdflib import URIRef
from rdflib.namespace import RDF

from src.extraction_prompt_generation.validate.report.common import (
    _graph_fingerprint,
    _local_name,
)
from src.extraction_prompt_generation.validate.report.stage.context import (
    StageProbe,
)


def probe_entity_creator_surface(probe: StageProbe) -> None:
    """Require the public create_* surface and probe external-class creators."""
    from src.extraction_prompt_generation.compile.operation_units import (
        owned_entity_tool_contracts as _owned_entity_tool_contracts,
    )

    graph = probe.graph
    assert graph is not None
    actual_creator_names = {
        symbol
        for symbol, value in vars(probe.imported_module).items()
        if symbol.startswith("create_") and callable(value)
    }
    expected_creator_names = {
        str(item.get("public_tool") or "")
        for item in _owned_entity_tool_contracts(probe.context)
        if str(item.get("public_tool") or "")
    }
    om2_range_iris = {
        str(range_iri)
        for item in probe.publish_contract.get("object_properties") or []
        for range_iri in item.get("range_iris") or []
        if "ontology-of-units-of-measure.org/resource/om-2/" in str(range_iri)
    }
    probe.om2_range_iris = om2_range_iris
    if om2_range_iris:
        expected_creator_names.add("create_om2_quantity")
    expected_creator_names.update(
        str((spec or {}).get("tool_name") or "").strip()
        for spec in probe.external_creator_specs
        if str((spec or {}).get("tool_name") or "").strip()
    )
    if actual_creator_names != expected_creator_names:
        probe.fail(
            f"{probe.name}: public creator surface differs from ontology-owned classes; "
            f"missing={sorted(expected_creator_names - actual_creator_names)}, "
            f"unexpected={sorted(actual_creator_names - expected_creator_names)}"
        )
    if om2_range_iris:
        om2_creator = getattr(probe.imported_module, "create_om2_quantity", None)
        om2_owner = str(getattr(om2_creator, "__module__", "")).rsplit(".", 1)[-1]
        if om2_owner != "_fixed_rdf_runtime":
            probe.fail(
                f"{probe.name}: create_om2_quantity must be imported directly from "
                "_fixed_rdf_runtime; local wrappers or definitions are forbidden"
            )
    for spec in probe.external_creator_specs:
        tool_name = str((spec or {}).get("tool_name") or "").strip()
        class_iri = str((spec or {}).get("class_iri") or "").strip()
        creator = getattr(probe.imported_module, tool_name, None)
        if not tool_name or not class_iri or not callable(creator):
            continue
        before = _graph_fingerprint(graph)
        try:
            payload = json.loads(creator(f"Validator {tool_name}"))
            created_iri = str(payload.get("iri") or "")
        except Exception as exc:
            probe.fail(
                f"{probe.name}: {tool_name} external-class behavior probe failed: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        if not created_iri or (
            URIRef(created_iri),
            RDF.type,
            URIRef(class_iri),
        ) not in graph:
            probe.fail(
                f"{probe.name}: {tool_name} must create exact external rdf:type "
                f"{class_iri}"
            )
        if _graph_fingerprint(graph) == before:
            probe.fail(
                f"{probe.name}: {tool_name} reported success without graph mutation"
            )


def probe_om2_quantity(probe: StageProbe) -> None:
    """Accept an allowed OM-2 range class and reject an unapproved one."""
    if not probe.om2_range_iris:
        return
    graph = probe.graph
    assert graph is not None
    om2_creator = getattr(probe.imported_module, "create_om2_quantity", None)
    if not callable(om2_creator):
        return
    allowed_class = sorted(probe.om2_range_iris)[0]
    before = _graph_fingerprint(graph)
    try:
        raw_result = om2_creator(allowed_class, "1 s")
        result = json.loads(raw_result)
        quantity_iri = str(result.get("iri") or "")
    except Exception as exc:
        probe.fail(
            f"{probe.name}: create_om2_quantity valid probe failed: "
            f"{type(exc).__name__}: {exc}"
        )
    else:
        if (
            not quantity_iri
            or (
                URIRef(quantity_iri),
                RDF.type,
                URIRef(allowed_class),
            )
            not in graph
            or before == _graph_fingerprint(graph)
        ):
            probe.fail(
                f"{probe.name}: create_om2_quantity did not create the allowed "
                "T-Box range class"
            )
    before = _graph_fingerprint(graph)
    try:
        rejected_raw = om2_creator(
            "https://example.invalid/NotAllowed",
            "1 s",
        )
    except Exception:
        if before != _graph_fingerprint(graph):
            probe.fail(
                f"{probe.name}: create_om2_quantity mutated graph while rejecting "
                "an unapproved quantity class"
            )
    else:
        try:
            rejected_result = json.loads(rejected_raw)
        except (TypeError, json.JSONDecodeError):
            rejected_result = {}
        rejected = (
            isinstance(rejected_result, dict)
            and rejected_result.get("status") in {"rejected", "error"}
        )
        if not rejected:
            probe.fail(
                f"{probe.name}: create_om2_quantity accepted a class outside the "
                "T-Box OM-2 range set"
            )
        if before != _graph_fingerprint(graph):
            probe.fail(
                f"{probe.name}: create_om2_quantity mutated graph while returning "
                "rejection for an unapproved quantity class"
            )


def probe_range_materialization(probe: StageProbe) -> None:
    """Require label parameters to materialize object-property targets."""
    graph = probe.graph
    assert graph is not None
    for prop in probe.publish_contract.get("object_properties") or []:
        predicate_iri = str(prop.get("property_iri") or "")
        property_local = _local_name(predicate_iri)
        ranges = [str(value) for value in prop.get("range_iris") or [] if str(value)]
        domains = [str(value) for value in prop.get("domain_iris") or [] if str(value)]
        if len(ranges) != 1 or not domains:
            continue
        range_iri = ranges[0]
        domain_local = _local_name(domains[0])
        creator = probe.creators.get(domain_local)
        if not callable(creator):
            continue
        parameter_name = f"{property_local}_label"
        if parameter_name not in inspect.signature(creator).parameters:
            continue
        before = _graph_fingerprint(graph)
        try:
            raw_result = creator(
                f"Validator {domain_local} {property_local}",
                **{parameter_name: f"Validator {property_local} target"},
            )
            result = json.loads(raw_result)
        except Exception as exc:
            probe.fail(
                f"{probe.name}: create_{domain_local} range-materialization probe for "
                f"{property_local} failed: {type(exc).__name__}: {exc}"
            )
            continue
        if not isinstance(result, dict) or result.get("status") != "ok":
            probe.fail(
                f"{probe.name}: create_{domain_local} could not materialize its "
                f"T-Box object-property target for {property_local}"
            )
            continue
        subject_iri = URIRef(str(result.get("iri") or ""))
        targets = list(graph.objects(subject_iri, URIRef(predicate_iri)))
        if not targets:
            probe.fail(
                f"{probe.name}: create_{domain_local} did not materialize "
                f"{property_local} from its label parameter"
            )
            continue
        if not any(
            (target, RDF.type, URIRef(range_iri)) in graph for target in targets
        ):
            observed_types = sorted(
                {
                    str(value)
                    for target in targets
                    for value in graph.objects(target, RDF.type)
                    if isinstance(value, URIRef)
                }
            )
            probe.fail(
                f"{probe.name}: create_{domain_local} materialized {property_local} "
                f"with target types {observed_types}, expected {range_iri}"
            )
        if before == _graph_fingerprint(graph):
            probe.fail(
                f"{probe.name}: create_{domain_local} range-materialization probe "
                f"for {property_local} did not mutate the retained graph"
            )


def validate_creation_entities_source(probe: StageProbe) -> None:
    """Static source-shape checks for the entities artifact."""
    from src.extraction_prompt_generation.compile.operation_units import (
        owned_entity_tool_contracts as _owned_entity_tool_contracts,
    )

    owned_class_locals = {
        str(contract.get("class_local") or "")
        for contract in _owned_entity_tool_contracts(probe.context)
        if str(contract.get("class_local") or "")
    }
    missing = [
        class_local
        for class_local in sorted(owned_class_locals)
        if f"def create_{class_local}" not in probe.text
    ]
    if missing:
        probe.fail(
            f"{probe.name}: missing stage create tools: "
            + ", ".join(f"create_{class_local}" for class_local in missing[:20])
        )
    forbidden_entity_apis = (
        "create_individual",
        "add_type",
        "GRAPH.add(",
        "retained_graph().add(",
    )
    used_forbidden = [api for api in forbidden_entity_apis if api in probe.text]
    if used_forbidden:
        probe.warn(
            f"{probe.name}: entity implementation uses internal generic capabilities via "
            + ", ".join(used_forbidden)
        )
    if "package_entity_capabilities" not in probe.text:
        probe.warn(
            f"{probe.name}: entity implementation does not use the optional package-bound capability helper"
        )
