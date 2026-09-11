"""Relationship-layer stage probes: `add_*` runtime writers and source shape.

Runs against `_creation_relationships.py`. See stage/README.md.
"""

from __future__ import annotations

import ast
import inspect

from rdflib import URIRef
from rdflib.namespace import RDF

from src.extraction_prompt_generation.validate.report.common import (
    _graph_fingerprint,
    _is_structured_rejection,
    _relationship_binding_evidence,
)
from src.extraction_prompt_generation.validate.report.stage.context import (
    StageProbe,
)
from src.extraction_prompt_generation.validate.report.tool_surface import (
    _relationship_param_description_report,
)


def probe_relationship_writers(probe: StageProbe) -> None:
    """Write each published object property and reject an incompatible range."""
    graph = probe.graph
    assert graph is not None
    relationship_probe_baseline = set(graph)
    for prop in probe.publish_contract.get("object_properties") or []:
        graph.remove((None, None, None))
        for triple in relationship_probe_baseline:
            graph.add(triple)
        predicate_iri = str(prop.get("property_iri") or "")
        property_local = predicate_iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        writer = getattr(probe.imported_module, f"add_{property_local}", None)
        if not callable(writer):
            continue
        domains = [str(value) for value in prop.get("domain_iris") or [] if str(value)]
        ranges = [str(value) for value in prop.get("range_iris") or [] if str(value)]
        if not domains or not ranges:
            continue
        domain_local = domains[0].rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        range_local = ranges[0].rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        subject_iri = probe.created.get(domain_local)
        if not subject_iri:
            continue
        # Probe each property with its own typed object occurrence.
        # Reusing the single class-level fixture across properties can
        # legitimately trigger OBJECT_OCCURRENCE_REUSE_FORBIDDEN for
        # non-reusable inputs/quantities owned by ordered steps.
        object_iri = f"urn:validator:{property_local}:{range_local}"
        graph.add(
            (
                URIRef(object_iri),
                RDF.type,
                URIRef(ranges[0]),
            )
        )
        signature = inspect.signature(writer)
        parameters = signature.parameters
        kwargs: dict[str, object] = {}
        if "object_iri" in parameters:
            kwargs["object_iri"] = object_iri
        subject_candidates = [
            parameter
            for parameter in parameters.values()
            if parameter.name != "object_iri"
            and parameter.default is inspect.Parameter.empty
        ]
        if not subject_candidates:
            probe.fail(
                f"{probe.name}: add_{property_local} has no explicit required subject parameter"
            )
            continue
        kwargs[subject_candidates[0].name] = subject_iri
        try:
            writer(**kwargs)
        except Exception as exc:
            probe.fail(
                f"{probe.name}: add_{property_local} rejected a T-Box-valid call: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        expected_triple = (
            URIRef(subject_iri),
            URIRef(predicate_iri),
            URIRef(object_iri),
        )
        if expected_triple not in graph:
            probe.fail(
                f"{probe.name}: add_{property_local} returned without writing its bound T-Box predicate"
            )
        incompatible = next(
            (
                iri
                for iri in sorted(probe.class_iris)
                if iri not in set(ranges) and iri not in set(domains)
            ),
            "",
        )
        if not incompatible:
            continue
        wrong_object = URIRef(f"urn:validator:wrong:{property_local}")
        graph.add((wrong_object, RDF.type, URIRef(incompatible)))
        wrong_kwargs = dict(kwargs)
        wrong_kwargs["object_iri"] = str(wrong_object)
        before_wrong = _graph_fingerprint(graph)
        rejected = False
        try:
            wrong_result = writer(**wrong_kwargs)
        except Exception:
            rejected = True
        else:
            rejected = _is_structured_rejection(wrong_result)
        after_wrong = _graph_fingerprint(graph)
        if not rejected:
            probe.fail(
                f"{probe.name}: add_{property_local} accepted a T-Box-incompatible range"
            )
        if before_wrong != after_wrong:
            probe.fail(
                f"{probe.name}: add_{property_local} mutated the graph during a rejected wrong-range call"
            )
    graph.remove((None, None, None))
    for triple in relationship_probe_baseline:
        graph.add(triple)


def validate_creation_relationships_source(probe: StageProbe) -> None:
    """Static source-shape and parameter-metadata checks for relationships."""
    from src.extraction_prompt_generation.compile.operation_units import (
        standalone_relationship_tool_contracts,
    )

    relationship_contracts = standalone_relationship_tool_contracts(
        probe.context.contract.get("relationship_tool_contracts") or {},
        probe.context.contract.get("materialization_operation_units") or {},
    )
    object_properties = sorted(relationship_contracts)
    missing = [
        prop_local
        for prop_local in object_properties
        if f"def add_{prop_local}" not in probe.text
    ]
    if missing:
        probe.fail(
            f"{probe.name}: missing stage relationship tools: "
            + ", ".join(f"add_{prop_local}" for prop_local in missing[:20])
        )
    try:
        relationship_tree = ast.parse(probe.text, filename=str(probe.path))
    except SyntaxError:
        relationship_tree = None
    if relationship_tree is not None:
        functions = {
            node.name: node
            for node in relationship_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for prop_local, contract in sorted(relationship_contracts.items()):
            function = functions.get(f"add_{prop_local}")
            if function is None:
                continue
            expected_predicate = str((contract or {}).get("predicate_iri") or "").strip()
            range_iris = {
                str(value).strip()
                for value in (contract or {}).get("range_iris") or []
                if str(value).strip()
            }
            binding = _relationship_binding_evidence(
                function,
                module=relationship_tree,
            )
            bound_iris = set(binding["bound_iris"])
            if binding["call_count"] != 1:
                probe.fail(
                    f"{probe.name}: add_{prop_local} must perform exactly one relationship "
                    "capability call that receives both subject_iri and object_iri; "
                    f"observed {binding['call_count']}"
                )
            if (
                expected_predicate
                and bound_iris
                and expected_predicate not in bound_iris
            ):
                probe.fail(
                    f"{probe.name}: add_{prop_local} must bind predicate IRI "
                    f"{expected_predicate}, observed {sorted(bound_iris) or ['none']}"
                )
            elif expected_predicate and not bound_iris:
                probe.warn(
                    f"{probe.name}: add_{prop_local} predicate binding could not be proven "
                    "by conservative static analysis; runtime behavior probes remain "
                    "authoritative"
                )
            mistaken_ranges = sorted(bound_iris & range_iris)
            if mistaken_ranges:
                probe.fail(
                    f"{probe.name}: add_{prop_local} binds range class IRI as predicate: "
                    + ", ".join(mistaken_ranges)
                )
    forbidden_mutation_apis = (
        "add_object_property",
        "add_object_triple",
        ".add((",
        "Graph.add(",
    )
    used_forbidden = [api for api in forbidden_mutation_apis if api in probe.text]
    if used_forbidden:
        probe.warn(
            f"{probe.name}: relationship implementation uses internal generic capabilities via "
            + ", ".join(used_forbidden)
        )
    if "package_relationship_capabilities" not in probe.text:
        probe.warn(
            f"{probe.name}: relationship implementation does not use the optional package-bound capability helper"
        )
    metadata_failures, metadata_warnings = _relationship_param_description_report(
        probe.context
    )
    probe.failures.extend(metadata_failures)
    probe.warnings.extend(metadata_warnings)
