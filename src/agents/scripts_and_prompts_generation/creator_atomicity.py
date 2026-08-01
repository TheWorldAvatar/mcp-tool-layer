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


def creator_call_recipe(
    contract: Mapping[str, Any],
    creator: Any,
    *,
    label: str,
    include_optional_datatypes: bool = True,
) -> dict[str, Any]:
    """Build one valid call from the projected contract and actual signature."""
    signature = inspect.signature(creator)
    kwargs: dict[str, Any] = {}
    if "label" in signature.parameters:
        kwargs["label"] = label
    if contract.get("ordered_member") and "order" in signature.parameters:
        kwargs["order"] = 1
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
    missing_required = [
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
    if missing_required:
        raise ValueError(
            f"{contract.get('public_tool') or creator.__name__} has required parameters "
            f"not represented by its creator contract: {missing_required}"
        )
    return {"args": [], "kwargs": kwargs, "signature": str(signature)}


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
            expected_names = {
                str(item.get("property_local") or "")
                for item in datatype_inputs
                if not item.get("required")
            }
            missing = sorted(expected_names - set(signature.parameters))
            valid_kwargs: dict[str, Any] = {}
            recipe = creator_call_recipe(
                contract,
                creator,
                label=f"Atomic creator probe {index}",
            )
            valid_kwargs.update(recipe["kwargs"])
            valid_kwargs.pop("label", None)
            result: dict[str, Any] = {
                "ok": not missing,
                "signature": str(signature),
                "missing_inputs": missing,
                "contract_inputs": datatype_inputs,
            }
            if missing:
                result.update(
                    phase="signature",
                    repair_hint=(
                        "Add every datatype_inputs entry to the creator's explicit typed "
                        "signature, including inherited domain properties."
                    ),
                )
                creator_results[tool_name] = result
                continue

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
                if not item.get("required")
            }
            result["valid_call"] = {
                "arguments": {"label": label, **valid_kwargs},
                "result": valid_result,
                "wrote_expected_datatypes": written,
            }
            if not valid_ok or not all(written.values()):
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
                item for item in datatype_inputs if not item.get("required")
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
