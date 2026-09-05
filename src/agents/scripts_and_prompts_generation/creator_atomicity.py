"""Contract-derived runtime probes for generated entity creators."""

from __future__ import annotations

import inspect
import json
from typing import Any, Mapping

from rdflib import URIRef


_VALID_VALUES = {
    "str": "Atomic probe value",
    "bool": True,
    "int": 2,
    "float": 2.5,
}
_INVALID_VALUES = {
    "str": 7,
    "bool": "not-a-boolean",
    "int": "not-an-integer",
    "float": "not-a-float",
}


def resolve_ordering_parameter_name(
    contract: Mapping[str, Any],
    signature: inspect.Signature,
) -> str:
    """Best-effort public parameter bound to the ordering property.

    Name equality is observational only. Ambiguous or freely named parameters
    are left unbound so LLM semantic review can judge the binding.
    """
    if not contract.get("ordered_member"):
        return ""
    ordering_local = str(contract.get("ordering_property_local") or "")
    if ordering_local in signature.parameters:
        return ordering_local
    non_ordering_contract_names = {
        str(item.get("property_local") or "")
        for item in contract.get("datatype_inputs") or []
        if str(item.get("property_local") or "") != ordering_local
    }
    candidates = [
        parameter.name
        for parameter in signature.parameters.values()
        if parameter.name != "label"
        and parameter.name not in non_ordering_contract_names
        and parameter.default is inspect.Parameter.empty
        and parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
    ]
    if len(candidates) != 1:
        return ""
    return candidates[0]


def parameter_binding_evidence(
    contract: Mapping[str, Any],
    signature: inspect.Signature,
) -> dict[str, Any]:
    """Project T-Box properties onto the actual public signature without judging."""
    ordering_local = str(contract.get("ordering_property_local") or "")
    ordering_parameter = resolve_ordering_parameter_name(contract, signature)
    bindings: list[dict[str, Any]] = []
    for item in contract.get("datatype_inputs") or []:
        property_local = str(item.get("property_local") or "")
        if not property_local:
            continue
        if property_local == ordering_local:
            bound_name = ordering_parameter
        elif property_local in signature.parameters:
            bound_name = property_local
        else:
            bound_name = ""
        bindings.append(
            {
                "property_local": property_local,
                "property_iri": str(item.get("property_iri") or ""),
                "required": bool(item.get("required")),
                "python_type": str(item.get("python_type") or "str"),
                "bound_parameter": bound_name,
                "name_matches_tbox": bound_name == property_local,
            }
        )
    return {
        "public_tool": str(contract.get("public_tool") or ""),
        "signature": str(signature),
        "ordering_property_local": ordering_local,
        "ordering_parameter": ordering_parameter,
        "bindings": bindings,
        "unbound_properties": [
            item["property_local"]
            for item in bindings
            if not item["bound_parameter"]
        ],
    }


def creator_call_recipe(
    contract: Mapping[str, Any],
    creator: Any,
    *,
    label: str,
    include_optional_datatypes: bool = True,
) -> dict[str, Any]:
    """Build a best-effort call from names that already match the signature."""
    signature = inspect.signature(creator)
    kwargs: dict[str, Any] = {}
    if "label" in signature.parameters:
        kwargs["label"] = label
    ordering_name = resolve_ordering_parameter_name(contract, signature)
    if contract.get("ordered_member") and ordering_name:
        kwargs[ordering_name] = 1
    for item in contract.get("datatype_inputs") or []:
        name = str(item.get("property_local") or "")
        if (
            not name
            or name == str(contract.get("ordering_property_local") or "")
            or name not in signature.parameters
            or (not include_optional_datatypes and not item.get("required"))
        ):
            continue
        kwargs[name] = _VALID_VALUES[str(item.get("python_type") or "str")]
    for edge in contract.get("required_edges") or []:
        if edge.get("target_resolution") == "existing_iri_parameter":
            name = str(edge.get("parameter_name") or "")
            if name in signature.parameters:
                kwargs[name] = "urn:atomic-probe:existing-owner"
        if edge.get("target_resolution") == "same_operation_create":
            label_name = str(edge.get("label_parameter") or "")
            if label_name in signature.parameters:
                kwargs[label_name] = f"{label} dependent"
            for dependent_input in edge.get("datatype_inputs") or []:
                name = str(dependent_input.get("parameter_name") or "")
                if (
                    name
                    and name in signature.parameters
                    and (
                        include_optional_datatypes
                        or dependent_input.get("required")
                    )
                ):
                    kwargs[name] = _VALID_VALUES[
                        str(dependent_input.get("python_type") or "str")
                    ]
    unbound_required = [
        parameter.name
        for parameter in signature.parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in {
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }
        and parameter.name not in kwargs
    ]
    return {
        "args": [],
        "kwargs": kwargs,
        "signature": str(signature),
        "ordering_parameter": ordering_name,
        "unbound_required": unbound_required,
        "parameter_bindings": parameter_binding_evidence(contract, signature),
    }


def _structured(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _restore_graph(graph: Any, snapshot: set[Any]) -> None:
    graph.remove((None, None, None))
    for triple in snapshot:
        graph.add(triple)


def probe_generated_creator_atomicity(
    *,
    module: Any,
    runtime: Any,
    creator_contracts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Exercise every contract-owned creator with valid and invalid complex calls."""
    graph = runtime.retained_graph()
    package_snapshot = set(graph)
    creator_results: dict[str, Any] = {}
    try:
        for index, contract in enumerate(creator_contracts, start=1):
            tool_name = str(contract.get("public_tool") or "")
            creator = getattr(module, tool_name, None)
            if not callable(creator):
                creator_results[tool_name] = {
                    "ok": False,
                    "phase": "surface",
                    "repair_hint": "Define the exact contract-owned public creator.",
                }
                continue
            signature = inspect.signature(creator)
            datatype_inputs = list(contract.get("datatype_inputs") or [])
            recipe = creator_call_recipe(
                contract,
                creator,
                label=f"Atomic creator probe {index}",
            )
            bindings = recipe["parameter_bindings"]
            valid_kwargs: dict[str, Any] = dict(recipe["kwargs"])
            valid_kwargs.pop("label", None)
            for edge in contract.get("required_edges") or []:
                if edge.get("target_resolution") != "existing_iri_parameter":
                    continue
                parameter_name = str(edge.get("parameter_name") or "")
                container_classes = [
                    str(value)
                    for value in edge.get("container_class_iris") or []
                    if str(value)
                ]
                if parameter_name and container_classes:
                    parent_iri = runtime.package_entity_capabilities()[
                        container_classes[0]
                    ](f"Atomic parent probe {index}")
                    valid_kwargs[parameter_name] = parent_iri
            supplied_properties = {
                str(item.get("property_local") or "")
                for item in datatype_inputs
                if str(item.get("property_local") or "") in valid_kwargs
                or (
                    str(item.get("property_local") or "")
                    == str(contract.get("ordering_property_local") or "")
                    and recipe.get("ordering_parameter")
                    and recipe["ordering_parameter"] in recipe["kwargs"]
                )
            }
            result: dict[str, Any] = {
                "ok": True,
                "signature": str(signature),
                "parameter_bindings": bindings,
                "unbound_required": recipe.get("unbound_required") or [],
                "contract_inputs": datatype_inputs,
            }

            before_valid = set(graph)
            label = f"Atomic creator probe {index}"
            try:
                valid_raw = creator(label, **valid_kwargs)
                valid_result = _structured(valid_raw)
            except Exception as exc:
                valid_result = {"exception": f"{type(exc).__name__}: {exc}"}
            iri = str(valid_result.get("iri") or "")
            valid_ok = valid_result.get("status") == "ok" and bool(iri)
            written = {
                str(item.get("property_local") or ""): bool(
                    iri
                    and list(
                        graph.objects(
                            URIRef(iri), URIRef(str(item.get("property_iri") or ""))
                        )
                    )
                )
                for item in datatype_inputs
                if str(item.get("property_local") or "") in supplied_properties
            }
            result["valid_call"] = {
                "arguments": {"label": label, **valid_kwargs},
                "result": valid_result,
                "supplied_properties": sorted(supplied_properties),
                "wrote_expected_datatypes": written,
            }
            required_edge_results: list[dict[str, Any]] = []
            if iri:
                for edge in contract.get("required_edges") or []:
                    predicate_iri = str(edge.get("predicate_iri") or "")
                    if (
                        edge.get("direction")
                        == "container_as_subject_owner_as_object"
                    ):
                        present = bool(
                            list(
                                graph.subjects(
                                    URIRef(predicate_iri), URIRef(iri)
                                )
                            )
                        )
                    else:
                        present = bool(
                            list(
                                graph.objects(
                                    URIRef(iri), URIRef(predicate_iri)
                                )
                            )
                        )
                    required_edge_results.append(
                        {
                            "predicate_iri": predicate_iri,
                            "direction": edge.get("direction"),
                            "present": present,
                        }
                    )
            result["valid_call"]["required_edges"] = required_edge_results
            if not valid_ok:
                result.update(
                    ok=False,
                    phase="valid_call",
                    repair_hint=(
                        "A complete contract-valid creator call must return status ok "
                        "with the owner IRI."
                    ),
                )
            if valid_ok and required_edge_results and not all(
                item["present"] for item in required_edge_results
            ):
                result.update(
                    ok=False,
                    phase="valid_required_edges",
                    repair_hint=(
                        "Write every contract-declared required edge inside the same "
                        "atomic creator transaction."
                    ),
                )
            if valid_ok and written and not all(written.values()):
                result.update(
                    ok=False,
                    phase="valid_call",
                    repair_hint=(
                        "Route every supplied datatype input to its exact bound writer after "
                        "all inputs have passed prevalidation."
                    ),
                )
                _restore_graph(graph, before_valid)
                creator_results[tool_name] = result
                continue

            optional_inputs = [
                item
                for item in datatype_inputs
                if not item.get("required")
                and str(item.get("property_local") or "") in signature.parameters
            ]
            if optional_inputs:
                invalid_item = optional_inputs[-1]
                invalid_name = str(invalid_item.get("property_local") or "")
                invalid_kwargs = dict(valid_kwargs)
                invalid_kwargs[invalid_name] = _INVALID_VALUES[
                    str(invalid_item.get("python_type") or "str")
                ]

                new_before = set(graph)
                try:
                    invalid_raw = creator(
                        f"Atomic invalid new probe {index}", **invalid_kwargs
                    )
                    invalid_result = _structured(invalid_raw)
                except Exception as exc:
                    invalid_result = {"exception": f"{type(exc).__name__}: {exc}"}
                new_unchanged = set(graph) == new_before

                existing_before = set(graph)
                try:
                    existing_raw = creator(label, **invalid_kwargs)
                    existing_result = _structured(existing_raw)
                except Exception as exc:
                    existing_result = {"exception": f"{type(exc).__name__}: {exc}"}
                existing_unchanged = set(graph) == existing_before
                result["invalid_call"] = {
                    "invalid_parameter": invalid_name,
                    "new_entity_result": invalid_result,
                    "new_entity_graph_unchanged": new_unchanged,
                    "existing_entity_result": existing_result,
                    "existing_entity_graph_unchanged": existing_unchanged,
                }
                if not new_unchanged or not existing_unchanged:
                    result.update(
                        ok=False,
                        phase="invalid_input_no_mutation",
                        repair_hint=(
                            "Prevalidate label, order, and every non-None datatype input "
                            "before calling any entity, ordered-entity, or datatype mutator. "
                            "Return error_json immediately on invalid input."
                        ),
                    )
            edge_parameters = [
                str(edge.get("parameter_name") or "")
                for edge in contract.get("required_edges") or []
                if edge.get("target_resolution") == "existing_iri_parameter"
                and str(edge.get("parameter_name") or "") in signature.parameters
            ]
            if edge_parameters:
                invalid_name = edge_parameters[0]
                invalid_kwargs = dict(valid_kwargs)
                invalid_kwargs[invalid_name] = "not-an-absolute-iri"
                edge_before = set(graph)
                try:
                    invalid_edge_raw = creator(
                        f"Atomic invalid edge probe {index}", **invalid_kwargs
                    )
                    invalid_edge_result = _structured(invalid_edge_raw)
                except Exception as exc:
                    invalid_edge_result = {
                        "exception": f"{type(exc).__name__}: {exc}"
                    }
                edge_unchanged = set(graph) == edge_before
                result["invalid_required_edge_call"] = {
                    "invalid_parameter": invalid_name,
                    "result": invalid_edge_result,
                    "graph_unchanged": edge_unchanged,
                }
                if not edge_unchanged:
                    result.update(
                        ok=False,
                        phase="invalid_required_edge_no_mutation",
                        repair_hint=(
                            "Wrap owner, dependent, datatype, and required-edge "
                            "mutations in one atomic graph transaction."
                        ),
                    )
            creator_results[tool_name] = result
    finally:
        _restore_graph(graph, package_snapshot)

    failures = {
        name: evidence
        for name, evidence in creator_results.items()
        if not evidence.get("ok")
    }
    return {
        "ok": not failures,
        "probe_kind": "creator_atomicity",
        "creators": creator_results,
        "failures": failures,
    }
