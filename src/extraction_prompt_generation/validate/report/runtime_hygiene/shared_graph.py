"""Runtime probe: create and export tools must share the retained graph.

Also forbids exposing aggregate `materialize_hints`. See README.md.
"""

from __future__ import annotations

import importlib
import inspect
import json
from collections.abc import Mapping
from typing import Any

from rdflib import Graph

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.validate.report.common import (
    _seed_existing_operation_targets,
    _structured_result,
)
from src.extraction_prompt_generation.validate.report.runtime_hygiene.accumulator import (
    HygieneState,
)


def probe_create_export_shared_graph(
    context: AgenticGenerationContext,
    module: Any,
    state: HygieneState,
) -> bool:
    """Return False when the caller should stop after this probe."""
    materialize = getattr(module, "materialize_hints", None)
    if materialize is not None:
        state.fail(
            "tool:materialize_hints#forbidden-exposure",
            "Generated package exposes forbidden aggregate `materialize_hints`; expose only "
            "atomic create/add/check tools plus init_memory and export_memory",
            subject_kind="tool",
            tool_name="materialize_hints",
        )
        return False

    create_tools = sorted(
        (name, value)
        for name, value in vars(module).items()
        if name.startswith("create_") and callable(value)
    )
    export_memory = getattr(module, "export_memory", None)
    init_memory = getattr(module, "init_memory", None)
    if not (create_tools and callable(export_memory) and callable(init_memory)):
        return True
    runtime = getattr(module, "rdf_runtime", None)
    if runtime is None and module.__package__:
        runtime = importlib.import_module(f"{module.__package__}._fixed_rdf_runtime")
    graph = runtime.retained_graph() if runtime is not None else None
    snapshot = str(graph.serialize(format="nt")) if isinstance(graph, Graph) else None
    try:
        init_signature = inspect.signature(init_memory)
        if len(init_signature.parameters) >= 2:
            init_memory("validator-doi", "Validator Top")
        elif len(init_signature.parameters) == 1:
            init_memory("validator-doi")
        else:
            init_memory()
        from src.extraction_prompt_generation.validate.creator_atomicity import (
            creator_call_recipe,
        )
        from src.extraction_prompt_generation.compile.operation_units import (
            owned_entity_tool_contracts as _owned_entity_tool_contracts,
        )

        creator_contracts = {
            str(item.get("public_tool") or ""): item
            for item in _owned_entity_tool_contracts(context)
        }
        selected = next(
            (
                (name, tool, creator_contracts[name])
                for name, tool in create_tools
                if name in creator_contracts
            ),
            None,
        )
        if selected is None:
            raise ValueError(
                "No ontology-owned create tool has a projected creator contract"
            )
        create_name, create_tool, create_contract = selected
        call_recipe = creator_call_recipe(
            create_contract,
            create_tool,
            label="Validator shared graph probe",
            include_optional_datatypes=False,
        )
        if isinstance(graph, Graph):
            _seed_existing_operation_targets(
                graph, create_contract, call_recipe["kwargs"]
            )
        raw_created = create_tool(
            *call_recipe["args"],
            **call_recipe["kwargs"],
        )
        created_result = _structured_result(raw_created)
        created_iri = str(created_result.get("iri") or "").strip()
        if created_result.get("status") != "ok" or not created_iri:
            raise ValueError(
                f"{create_name} did not return a successful envelope with an IRI"
            )
        export_signature = inspect.signature(export_memory)
        export_kwargs: dict[str, Any] = {}
        if "doi" in export_signature.parameters:
            export_kwargs["doi"] = "validator-doi"
        if "top_level_entity_name" in export_signature.parameters:
            export_kwargs["top_level_entity_name"] = "Validator Top"
        if "scope" in export_signature.parameters:
            export_kwargs["scope"] = "Validator Top"
        if "top_iri" in export_signature.parameters:
            export_kwargs["top_iri"] = str(created_iri)
        missing_export_parameters = [
            parameter.name
            for parameter in export_signature.parameters.values()
            if parameter.default is inspect.Parameter.empty
            and parameter.kind
            in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
            and parameter.name not in export_kwargs
        ]
        if missing_export_parameters:
            raise ValueError(
                "export_memory has required parameters unsupported by the lifecycle "
                f"contract probe: {missing_export_parameters}"
            )
        exported = export_memory(**export_kwargs)
        if isinstance(exported, str):
            try:
                exported = json.loads(exported)
            except json.JSONDecodeError:
                exported = {"ttl": exported}
        exported_ttl = (
            str(exported.get("ttl") or exported.get("turtle") or "")
            if isinstance(exported, Mapping)
            else ""
        )
        if not exported_ttl.strip() or str(created_iri) not in exported_ttl:
            state.fail(
                "runtime-policy:create-export-shared-graph",
                "Generated create/export tools do not share graph state: "
                f"`{create_name}` created an IRI absent from exported Turtle",
                subject_kind="runtime-policy",
                create_tool=create_name,
                export_tool="export_memory",
                create_call_recipe=call_recipe,
            )
            return False
    except Exception as exc:
        state.fail(
            "runtime-policy:create-export-shared-graph",
            "Generated create/export shared-graph probe failed: "
            f"{type(exc).__name__}: {exc}",
            subject_kind="runtime-policy",
        )
        return False
    finally:
        if isinstance(graph, Graph) and snapshot is not None:
            graph.remove((None, None, None))
            if snapshot.strip():
                graph.parse(data=snapshot, format="nt")
    return True
