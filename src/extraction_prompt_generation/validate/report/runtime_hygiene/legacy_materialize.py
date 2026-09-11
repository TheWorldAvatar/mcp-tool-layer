"""Unreachable `materialize_hints` graph audit; kept for historical parity.

Do not add new callers. See README.md.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import tempfile
from collections.abc import Mapping
from typing import Any

from rdflib import Graph, URIRef
from rdflib.namespace import RDF, RDFS

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.validate.report.common import (
    _build_runtime_probe_hints,
)
from src.extraction_prompt_generation.validate.report.runtime_hygiene.accumulator import (
    HygieneState,
)


def audit_materialize_hints_graph(
    context: AgenticGenerationContext,
    module: Any,
    state: HygieneState,
) -> None:
    materialize = getattr(module, "materialize_hints", None)
    hints = _build_runtime_probe_hints(context)
    previous_data_dir = os.environ.get("TWA_AGENTIC_DATA_DIR")
    runtime = getattr(module, "rdf_runtime", None)
    if runtime is None and module.__package__:
        runtime = importlib.import_module(f"{module.__package__}._fixed_rdf_runtime")
    runtime_graph = runtime.retained_graph() if runtime is not None else None
    snapshot = (
        str(runtime_graph.serialize(format="nt"))
        if isinstance(runtime_graph, Graph)
        else None
    )
    materialized_graph = Graph()
    try:
        with tempfile.TemporaryDirectory(prefix="agentic_runtime_hygiene_") as tmp_dir:
            os.environ["TWA_AGENTIC_DATA_DIR"] = tmp_dir
            init_memory = getattr(module, "init_memory", None)
            if not callable(init_memory) and callable(getattr(init_memory, "fn", None)):
                init_memory = init_memory.fn
            if callable(init_memory):
                init_signature = inspect.signature(init_memory)
                if len(init_signature.parameters) >= 2:
                    init_memory("validator-doi", "Validator Top")
                elif len(init_signature.parameters) == 1:
                    init_memory("validator-doi")
                else:
                    init_memory()

            materialize_signature = inspect.signature(materialize)
            parameters = list(materialize_signature.parameters.values())
            accepts_varargs = any(
                parameter.kind == inspect.Parameter.VAR_POSITIONAL
                for parameter in parameters
            )
            positional_capacity = sum(
                parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                }
                for parameter in parameters
            )
            if accepts_varargs or positional_capacity >= 4:
                raw_result = materialize(
                    "validator-doi",
                    "validator-top",
                    "Validator Top",
                    json.dumps(hints, ensure_ascii=False),
                )
            elif positional_capacity >= 1:
                raw_result = materialize(hints)
            else:
                raw_result = materialize()

            result: dict[str, Any] = {}
            ttl = ""
            if isinstance(raw_result, Mapping):
                result = dict(raw_result)
            elif isinstance(raw_result, str):
                try:
                    parsed_result = json.loads(raw_result)
                except json.JSONDecodeError:
                    ttl = raw_result
                else:
                    if isinstance(parsed_result, Mapping):
                        result = dict(parsed_result)
                    elif isinstance(parsed_result, str):
                        ttl = parsed_result
            if result.get("status") not in {None, "ok", "success"}:
                state.fail(
                    "runtime-policy:materialize-status-ok",
                    "Generated materialize_hints failed runtime graph hygiene validation: "
                    + str(result.get("message") or result),
                    subject_kind="runtime-policy",
                )
                return

            ttl = str(result.get("ttl") or ttl)
            materialized_top_iri = str(result.get("top_iri") or "").strip()
            if not materialized_top_iri:
                state.fail(
                    "runtime-policy:materialize-top-iri",
                    "Generated materialize_hints did not return the materialized top_iri",
                    subject_kind="runtime-policy",
                )
                return
            if not ttl.strip():
                export_memory = getattr(module, "export_memory", None)
                if not callable(export_memory) and callable(
                    getattr(export_memory, "fn", None)
                ):
                    export_memory = export_memory.fn
                if callable(export_memory):
                    export_signature = inspect.signature(export_memory)
                    required_export_parameters = [
                        parameter
                        for parameter in export_signature.parameters.values()
                        if parameter.default is inspect.Parameter.empty
                        and parameter.kind
                        in {
                            inspect.Parameter.POSITIONAL_ONLY,
                            inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        }
                    ]
                    if not required_export_parameters:
                        exported = export_memory()
                        if isinstance(exported, Mapping):
                            ttl = str(exported.get("ttl") or "")
                        else:
                            ttl = str(exported or "")
            if not ttl.strip():
                state.fail(
                    "runtime-policy:nonempty-ttl",
                    "Generated package exposed no Turtle through materialize_hints or export_memory",
                    subject_kind="runtime-policy",
                )
                return
            materialized_graph.parse(data=ttl, format="turtle")
            if len(materialized_graph) == 0:
                state.fail(
                    "runtime-policy:materialize-abox-nonempty",
                    "Generated materialize_hints returned Turtle with no A-Box triples",
                    subject_kind="runtime-policy",
                )
                return
            if not any(
                materialized_graph.triples(
                    (URIRef(materialized_top_iri), RDF.type, None)
                )
            ):
                state.fail(
                    "runtime-policy:materialize-top-typing",
                    "Generated materialize_hints returned a top_iri without an A-Box rdf:type",
                    subject_kind="runtime-policy",
                    top_iri=materialized_top_iri,
                )
                return
    except Exception as exc:
        state.fail(
            "runtime-policy:materialization-probe",
            f"Generated runtime graph hygiene validation failed: {type(exc).__name__}: {exc}",
            subject_kind="runtime-policy",
        )
        return
    finally:
        if isinstance(runtime_graph, Graph) and snapshot is not None:
            runtime_graph.remove((None, None, None))
            if snapshot.strip():
                runtime_graph.parse(data=snapshot, format="nt")
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
    for subject, _, class_iri in materialized_graph.triples((None, RDF.type, None)):
        if not isinstance(subject, URIRef):
            continue
        class_local = class_iris.get(str(class_iri))
        if not class_local:
            continue
        typed_nodes.setdefault(subject, set()).add(class_local)
        for label in materialized_graph.objects(subject, RDFS.label):
            label_text = str(label or "").strip()
            if label_text:
                label_groups.setdefault((class_local, label_text), set()).add(subject)

    duplicate_labels = [
        f"{class_local}:{label}"
        for (class_local, label), nodes in sorted(label_groups.items())
        if len(nodes) > 1
    ]
    if duplicate_labels:
        state.fail(
            "runtime-policy:unique-same-class-label",
            "Generated runtime graph contains duplicate same-class labels after materialize_hints: "
            + ", ".join(duplicate_labels[:8]),
            subject_kind="runtime-policy",
        )

    top_iri = str(result.get("top_iri") or "").strip()
    if not top_iri:
        top_class_iri = str(
            (context.contract.get("top_entity") or {}).get("class_iri") or ""
        ).strip()
        if top_class_iri:
            top_iri = str(
                next(
                    materialized_graph.subjects(RDF.type, URIRef(top_class_iri)),
                    "",
                )
                or ""
            )
    reachable: set[URIRef] = set()
    if top_iri:
        frontier = [URIRef(top_iri)]
        while frontier:
            current = frontier.pop()
            if current in reachable:
                continue
            reachable.add(current)
            for _, predicate, obj in materialized_graph.triples(
                (current, None, None)
            ):
                if predicate in {RDF.type, RDFS.label}:
                    continue
                if isinstance(obj, URIRef) and obj not in reachable:
                    frontier.append(obj)
    else:
        state.warnings.append(
            "Runtime graph hygiene validation could not inspect reachability because top_iri is missing"
        )

    top_contract = context.contract.get("top_entity") or {}
    top_role_known = (
        str(top_contract.get("status") or "").casefold() not in {"", "unknown"}
        and bool(str(top_contract.get("class_iri") or "").strip())
    )
    if reachable and top_role_known:
        unreachable = [
            f"{sorted(classes)[0]}:{node}"
            for node, classes in sorted(
                typed_nodes.items(), key=lambda item: str(item[0])
            )
            if node not in reachable
        ]
        if unreachable:
            state.fail(
                "runtime-policy:reachable-from-top",
                "Generated runtime graph contains typed nodes unreachable from the materialized top entity: "
                + ", ".join(unreachable[:8]),
                subject_kind="runtime-policy",
            )
    elif reachable and not top_role_known:
        state.warnings.append(
            "Runtime reachability hard gate skipped because the active T-Box does not "
            "declare an authoritative top entity role"
        )

    om2_namespace = "http://www.ontology-of-units-of-measure.org/resource/om-2/"
    ontology_namespace = str(
        (context.parsed.get("ontology") or {}).get("namespace") or ""
    )
    non_om2_required_predicates = {
        str((item or {}).get("predicate_iri") or "").strip()
        for item in (context.contract.get("required_links") or [])
        if not str((item or {}).get("target_class_iri") or "").startswith(om2_namespace)
    }
    ontology_class_ranges = {
        str(predicate).strip()
        for class_spec in (context.parsed.get("classes") or {}).values()
        for predicate, range_local in (
            ((class_spec or {}).get("object_properties") or {}).items()
        )
        if str(range_local or "").strip()
        and not str(range_local or "").startswith(om2_namespace)
    }
    for spec in context.contract.get("om2_quantity_properties") or []:
        predicate_iri = URIRef(str((spec or {}).get("predicate_iri") or "").strip())
        range_iri = URIRef(str((spec or {}).get("range_iris") or "").strip())
        if not str(predicate_iri) or not str(range_iri):
            continue
        if (
            str(predicate_iri) in non_om2_required_predicates
            or str((spec or {}).get("predicate_local") or "").strip()
            in ontology_class_ranges
        ):
            state.warnings.append(
                f"OM-2 runtime probe skipped ambiguous predicate {predicate_iri}"
            )
            continue
        links = list(graph.triples((None, predicate_iri, None)))
        if not links:
            predicate_local = str((spec or {}).get("predicate_local") or predicate_iri)
            state.fail(
                f"property:{predicate_local}#om2-link",
                "Generated materializer did not emit OM-2 link for "
                + predicate_local,
                subject_kind="property",
                property_local=predicate_local,
            )
            continue
        conforming_quantities = [
            quantity
            for _, _, quantity in links
            if isinstance(quantity, URIRef)
            and (quantity, RDF.type, range_iri) in graph
        ]
        if not conforming_quantities:
            predicate_local = str((spec or {}).get("predicate_local") or predicate_iri)
            state.fail(
                f"property:{predicate_local}#om2-range-type",
                f"OM-2 predicate {predicate_iri} has no target of expected type {range_iri}",
                subject_kind="property",
                property_local=predicate_local,
            )
            continue
        for quantity in conforming_quantities:
            values = list(graph.objects(quantity, URIRef(om2_namespace + "hasNumericalValue")))
            units = list(graph.objects(quantity, URIRef(om2_namespace + "hasUnit")))
            if len(values) != 1:
                predicate_local = str((spec or {}).get("predicate_local") or predicate_iri)
                state.fail(
                    f"property:{predicate_local}#om2-single-numerical-value",
                    f"OM-2 quantity {quantity} must have exactly one numerical value",
                    subject_kind="property",
                    property_local=predicate_local,
                )
            if len(units) != 1 or not isinstance(units[0], URIRef):
                predicate_local = str((spec or {}).get("predicate_local") or predicate_iri)
                state.fail(
                    f"property:{predicate_local}#om2-single-iri-unit",
                    f"OM-2 quantity {quantity} must have exactly one IRI-valued unit",
                    subject_kind="property",
                    property_local=predicate_local,
                )
            if ontology_namespace and str(range_iri).startswith(ontology_namespace):
                predicate_local = str((spec or {}).get("predicate_local") or predicate_iri)
                state.fail(
                    f"property:{predicate_local}#external-range-namespace",
                    f"OM-2 quantity range must not use ontology namespace: {range_iri}",
                    subject_kind="property",
                    property_local=predicate_local,
                )
    return
