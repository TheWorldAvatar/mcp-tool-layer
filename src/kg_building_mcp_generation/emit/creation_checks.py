"""Emit *_creation_checks.py."""

from __future__ import annotations

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.compile.reuse_policy import (
    existing_entity_check_contracts,
)

def _checks_script(context: AgenticGenerationContext) -> str:
    check_contracts = existing_entity_check_contracts(
        parsed=context.parsed,
        contract=context.contract,
    )
    class_funcs = []
    manifest = ["check_ordered_members"]
    for spec in check_contracts:
        tool_name = str(spec["public_tool"])
        manifest.append(tool_name)
        class_funcs.append(
            f"""def {tool_name}(
    proposed_entity_json: str = "",
    *,
    label: str = "",
) -> str:
    if not proposed_entity_json and label:
        proposed_entity_json = json.dumps({{"label": label}}, ensure_ascii=False)
    return _check_existing(
        class_local={str(spec["class_local"])!r},
        class_iri={str(spec["class_iri"])!r},
        lookup_scope={str(spec["lookup_scope"])!r},
        reuse_authorized={bool(spec["reuse_authorized"])!r},
        reference_resolution_only={bool(spec["reference_resolution_only"])!r},
        reuse_scope={str(spec["reuse_scope"])!r},
        match_basis={str(spec["match_basis"])!r},
        class_contract={{
            "comment": {str(spec.get("class_comment") or "")!r},
            "datatype_properties": {dict(spec.get("datatype_properties") or {})!r},
            "object_properties": {dict(spec.get("object_properties") or {})!r},
        }},
        proposed_entity_json=proposed_entity_json,
    )
"""
        )
    return (
        """from __future__ import annotations

import json
from rdflib import BNode, Graph, Literal, RDF, RDFS, URIRef

from ._fixed_rdf_runtime import (
    current_memory_scope,
    load_central_reuse_memory,
    load_document_reuse_memory,
    register_central_reuse_authorization,
    retained_graph,
)
from ._reuse_pair_judge import judge_reuse_pairs

SKOS_PREF_LABEL = URIRef("http://www.w3.org/2004/02/skos/core#prefLabel")
GRAPH = Graph()
PROVENANCE = {{}}


def _labels(node) -> list[str]:
    values = {{
        str(value)
        for predicate in (RDFS.label, SKOS_PREF_LABEL)
        for value in GRAPH.objects(node, predicate)
        if isinstance(value, Literal) and str(value).strip()
    }}
    return sorted(values)


def _types(node) -> list[str]:
    return sorted(
        {{
            str(value)
            for value in GRAPH.objects(node, RDF.type)
            if isinstance(value, URIRef)
        }}
    )


def _literal_detail(value: Literal) -> dict:
    return {{
        "value": str(value),
        "datatype": str(value.datatype) if value.datatype else None,
        "language": value.language,
    }}


def _related_detail(node) -> dict:
    return {{
        "iri": str(node),
        "labels": _labels(node),
        "types": _types(node),
    }}


def _instance_detail(node: URIRef) -> dict:
    datatype_values: dict[str, list[dict]] = {{}}
    outgoing_relations: dict[str, list[dict]] = {{}}
    incoming_relations: dict[str, list[dict]] = {{}}
    for predicate, value in GRAPH.predicate_objects(node):
        if predicate == RDF.type or predicate in (RDFS.label, SKOS_PREF_LABEL):
            continue
        if isinstance(value, Literal):
            datatype_values.setdefault(str(predicate), []).append(
                _literal_detail(value)
            )
        elif isinstance(value, (URIRef, BNode)):
            outgoing_relations.setdefault(str(predicate), []).append(
                _related_detail(value)
            )
    for subject, predicate in GRAPH.subject_predicates(node):
        if isinstance(subject, (URIRef, BNode)):
            incoming_relations.setdefault(str(predicate), []).append(
                _related_detail(subject)
            )
    return {{
        "iri": str(node),
        "labels": _labels(node),
        "types": _types(node),
        "datatype_values": datatype_values,
        "outgoing_relations": outgoing_relations,
        "incoming_relations": incoming_relations,
        "central_provenance": PROVENANCE.get(str(node), []),
    }}


def _check_existing(
    *,
    class_local: str,
    class_iri: str,
    lookup_scope: str,
    reuse_authorized: bool,
    reference_resolution_only: bool,
    reuse_scope: str,
    match_basis: str,
    class_contract: dict,
    proposed_entity_json: str,
) -> str:
    global GRAPH, PROVENANCE
    if lookup_scope == "central":
        GRAPH, PROVENANCE = load_central_reuse_memory({ontology_name!r})
    elif lookup_scope == "document":
        GRAPH, PROVENANCE = load_document_reuse_memory({ontology_name!r})
    else:
        GRAPH, PROVENANCE = retained_graph(), {{}}
    cls = URIRef(class_iri)
    instances = sorted(
        {{
            subject
            for subject in GRAPH.subjects(RDF.type, cls)
            if isinstance(subject, URIRef)
        }},
        key=str,
    )
    details = [_instance_detail(node) for node in instances]
    if lookup_scope in {{"central", "document"}}:
        try:
            proposed = json.loads(proposed_entity_json)
        except (TypeError, json.JSONDecodeError):
            proposed = None
        if not isinstance(proposed, dict) or not proposed:
            return json.dumps(
                {{
                    "status": "rejected",
                    "code": "PROPOSED_ENTITY_EVIDENCE_REQUIRED",
                    "class": class_local,
                    "instances": [],
                }},
                ensure_ascii=False,
                sort_keys=True,
            )
        scope = current_memory_scope()
        requests = [
            {{
                "pair_id": f"p{{index:04d}}",
                "class_iri": class_iri,
                "class_local": class_local,
                "class_contract": class_contract,
                "reuse_policy": {{
                    "reuse_scope": reuse_scope,
                    "match_basis": match_basis,
                }},
                "current_context": scope,
                "proposed_entity": proposed,
                "candidate_entity": detail,
            }}
            for index, detail in enumerate(details, start=1)
        ]
        try:
            judgements = judge_reuse_pairs(requests)
        except Exception as exc:
            return json.dumps(
                {{
                    "status": "rejected",
                    "code": "REUSE_JUDGE_FAILED_CLOSED",
                    "class": class_local,
                    "message": f"{{type(exc).__name__}}: {{exc}}",
                    "instances": [],
                }},
                ensure_ascii=False,
                sort_keys=True,
            )
        authorized = []
        for request, detail, judgement in zip(requests, details, judgements):
            if judgement.get("reuse_authorized") is not True:
                continue
            token = register_central_reuse_authorization(
                candidate_iri=detail["iri"],
                pair_id=request["pair_id"],
                judgement=judgement,
            )
            authorized.append(
                {{
                    **detail,
                    "reuse_authorization_token": token,
                    "reuse_judgement": judgement,
                }}
            )
        details = authorized
    return json.dumps(
        {{
            "status": "ok",
            "class": class_local,
            "class_iri": class_iri,
            "lookup_scope": lookup_scope,
            "class_reuse_eligible": reuse_authorized,
            "reuse_authorized": bool(details) if lookup_scope in {{"central", "document"}} else False,
            "reference_resolution_only": reference_resolution_only,
            "reuse_scope": reuse_scope,
            "match_basis": match_basis,
            "instances": details,
        }},
        ensure_ascii=False,
        sort_keys=True,
    )

def check_ordered_members() -> str:
    return json.dumps(
        {{
            "status": "error",
            "ok": False,
            "code": "ORDERED_MEMBER_CHECK_NOT_SESSION_BOUND",
            "message": (
                "This legacy creation-check module has no bound ledger. "
                "Use the occurrence MCP inspect_ordered_members tool, whose "
                "implementation is compiled with the occurrence contracts."
            ),
            "violations": [],
            "retryable": False,
            "skippable": False,
        }},
        ensure_ascii=False,
        sort_keys=True,
    )

__all__ = {manifest!r}

""".format(
            ontology_name=context.ontology.name,
            manifest=manifest,
        )
        + "\n".join(class_funcs)
    )

