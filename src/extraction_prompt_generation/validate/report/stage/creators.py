"""Shared create-tool runtime probes used by every non-base stage artifact.

Projects publish-contract classes onto imported `create_*` callables.
See stage/README.md.
"""

from __future__ import annotations

import json

from rdflib import URIRef
from rdflib.namespace import RDF

from src.extraction_prompt_generation.validate.report.common import (
    _graph_fingerprint,
    _seed_existing_operation_targets,
)
from src.extraction_prompt_generation.validate.report.stage.context import (
    StageProbe,
)


def prepare_creator_surface(probe: StageProbe) -> None:
    """Project publish-contract classes onto imported create_* callables."""
    from src.extraction_prompt_generation.compile.operation_units import (
        owned_entity_tool_contracts as _owned_entity_tool_contracts,
    )

    publish_contract = probe.context.contract.get("ontology_publish_contract") or {}
    class_iris = {
        str(item.get("class_iri") or "")
        for item in publish_contract.get("classes") or []
        if str(item.get("class_iri") or "")
    }
    class_by_local = {
        iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]: iri for iri in class_iris
    }
    probe.publish_contract = publish_contract
    probe.class_iris = class_iris
    probe.class_by_local = class_by_local
    probe.external_creator_specs = list(
        probe.context.contract.get("external_class_creators") or []
    )
    probe.creators = {
        local: getattr(probe.imported_module, f"create_{local}", None)
        for local in class_by_local
    }
    creator_contracts = _owned_entity_tool_contracts(probe.context)
    probe.creator_contract_by_local = {
        str(contract.get("class_local") or ""): contract
        for contract in creator_contracts
    }


def probe_owned_creators(probe: StageProbe) -> None:
    """Call each ontology-owned creator and require a typed graph mutation."""
    from src.extraction_prompt_generation.validate.creator_atomicity import (
        creator_call_recipe,
    )

    graph = probe.graph
    assert graph is not None
    for local, class_iri in sorted(probe.class_by_local.items()):
        creator = probe.creators.get(local)
        if not callable(creator):
            continue
        try:
            contract = probe.creator_contract_by_local.get(local)
            if contract is not None:
                recipe = creator_call_recipe(
                    contract,
                    creator,
                    label=f"Validator {local}",
                    include_optional_datatypes=False,
                )
                _seed_existing_operation_targets(graph, contract, recipe["kwargs"])
                before = _graph_fingerprint(graph)
                raw_result = creator(
                    *recipe["args"],
                    **recipe["kwargs"],
                )
            else:
                before = _graph_fingerprint(graph)
                raw_result = creator(f"Validator {local}")
        except TypeError:
            # Freely named required parameters are judged by LLM review.
            continue
        except Exception as exc:
            probe.fail(
                f"{probe.name}: create_{local} behavior probe failed: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        if not isinstance(raw_result, str):
            probe.fail(
                f"{probe.name}: create_{local} must return a JSON string envelope"
            )
            continue
        try:
            result = json.loads(raw_result)
        except json.JSONDecodeError:
            probe.fail(f"{probe.name}: create_{local} returned a non-JSON string")
            continue
        if not isinstance(result, dict) or result.get("status") != "ok":
            probe.fail(
                f"{probe.name}: create_{local} did not return a successful standard envelope"
            )
            continue
        created_iri = str(result.get("iri") or "").strip()
        if not created_iri:
            probe.fail(f"{probe.name}: create_{local} success envelope has no IRI")
            continue
        if before == _graph_fingerprint(graph):
            probe.fail(
                f"{probe.name}: create_{local} returned without mutating the retained graph"
            )
        if (
            URIRef(created_iri),
            RDF.type,
            URIRef(class_iri),
        ) not in graph:
            probe.fail(
                f"{probe.name}: create_{local} did not assert its T-Box class in the retained graph"
            )
        else:
            probe.created[local] = created_iri
        class_spec = (probe.context.parsed.get("classes") or {}).get(local) or {}
        for parent_local in class_spec.get("parent_classes") or []:
            parent_spec = (
                (probe.context.parsed.get("classes") or {}).get(parent_local) or {}
            )
            parent_iri = str(parent_spec.get("iri") or "").strip()
            if parent_iri and (
                URIRef(created_iri),
                RDF.type,
                URIRef(parent_iri),
            ) not in graph:
                probe.fail(
                    f"{probe.name}: create_{local} did not explicitly assert ancestor "
                    f"type {parent_local}"
                )
