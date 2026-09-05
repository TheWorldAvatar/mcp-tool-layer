from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from rdflib import Graph, Literal, RDF, RDFS, URIRef

from src.agents.scripts_and_prompts_generation.occurrence_surface_inference import (
    _judge_missing,
    _validate_decisions,
    infer_occurrence_surface,
)
from src.agents.scripts_and_prompts_generation.occurrence_surface_scripts import (
    emit_occurrence_argument_ownership,
    emit_occurrence_loop_guard,
    emit_occurrence_main,
    emit_occurrence_operations,
)
from src.agents.scripts_and_prompts_generation.occurrence_surface_units import (
    collect_extension_bridge_class_iris,
    compile_fallback_instruction,
    compile_loop_guard_contract,
    compile_occurrence_surface,
    discover_occurrence_surface_candidates,
    is_deterministic_candidate,
    mentioned_tool_names,
    public_tool_names,
)


NS = "https://example.test/ontology/"
XSD = "http://www.w3.org/2001/XMLSchema#"
OM2 = "http://www.ontology-of-units-of-measure.org/resource/om-2/Duration"


def _fixture() -> tuple[dict, dict]:
    parsed = {
        "classes": {
            "Container": {
                "iri": NS + "Container",
                "parent_classes": [],
                "comment": "Root collection that already exists after session init.",
            },
            "Member": {
                "iri": NS + "Member",
                "parent_classes": [],
                "comment": "One ordered occurrence heading inside the container.",
            },
            "Dependent": {
                "iri": NS + "Dependent",
                "parent_classes": [],
                "comment": "Fresh owner-local dependent of one Member heading.",
            },
            "Descriptor": {
                "iri": NS + "Descriptor",
                "parent_classes": [],
                "comment": "Reusable descriptor shared by exact label.",
            },
            "NestedType": {
                "iri": NS + "NestedType",
                "parent_classes": [],
                "comment": "Reusable type of a descriptor.",
            },
        },
        "properties": {
            "orderValue": {"comment": "Single-valued order of each member."},
            "dependentText": {"comment": "Optional text on the fresh dependent."},
            "containsMember": {
                "comment": "Every ordered member belongs to exactly one container."
            },
            "ownsDependent": {
                "comment": "Optional heading-local dependent created with the member."
            },
            "containsDependent": {
                "comment": "Root-owned dependent occurrence attached to the container."
            },
            "usesDescriptor": {
                "comment": "Optional reusable descriptor of this heading or root."
            },
            "hasNestedType": {
                "comment": "Reusable type attached to a descriptor, not a new heading."
            },
            "hasDuration": {
                "comment": "Optional duration quantity of this member heading."
            },
        },
    }
    contract = {
        "top_entity": {
            "class_local": "Container",
            "class_iri": NS + "Container",
        },
        "ontology_publish_contract": {
            "subclass_closure": [
                {"class_iri": NS + "Member", "superclass_iris": []},
                {"class_iri": NS + "Dependent", "superclass_iris": []},
                {"class_iri": NS + "Descriptor", "superclass_iris": []},
                {"class_iri": NS + "NestedType", "superclass_iris": []},
                {"class_iri": NS + "Container", "superclass_iris": []},
            ],
            "datatype_properties": [
                {
                    "property_iri": NS + "orderValue",
                    "domain_iris": [NS + "Member"],
                    "range_iris": [XSD + "integer"],
                },
                {
                    "property_iri": NS + "dependentText",
                    "domain_iris": [NS + "Dependent"],
                    "range_iris": [XSD + "string"],
                },
            ],
        },
        "ordered_member_profile": {
            "ordered_member_classes": ["Member"],
            "single_valued_ordering_properties": ["orderValue"],
            "individually_linked_object_properties": ["containsMember"],
        },
        "relationship_tool_contracts": {
            "containsMember": {
                "predicate_local": "containsMember",
                "predicate_iri": NS + "containsMember",
                "domain_iris": [NS + "Container"],
                "range_iris": [NS + "Member"],
            },
            "ownsDependent": {
                "predicate_local": "ownsDependent",
                "predicate_iri": NS + "ownsDependent",
                "domain_iris": [NS + "Member"],
                "range_iris": [NS + "Dependent"],
            },
            "containsDependent": {
                "predicate_local": "containsDependent",
                "predicate_iri": NS + "containsDependent",
                "domain_iris": [NS + "Container"],
                "range_iris": [NS + "Dependent"],
            },
            "usesDescriptor": {
                "predicate_local": "usesDescriptor",
                "predicate_iri": NS + "usesDescriptor",
                "domain_iris": [NS + "Member", NS + "Container"],
                "range_iris": [NS + "Descriptor"],
            },
            "hasNestedType": {
                "predicate_local": "hasNestedType",
                "predicate_iri": NS + "hasNestedType",
                "domain_iris": [NS + "Descriptor"],
                "range_iris": [NS + "NestedType"],
            },
            "hasDuration": {
                "predicate_local": "hasDuration",
                "predicate_iri": NS + "hasDuration",
                "domain_iris": [NS + "Member"],
                "range_iris": [OM2],
                "fixed_runtime_range_iris": [OM2],
            },
        },
        "reuse_policy": {
            "classes": [
                {
                    "class_iri": NS + "Dependent",
                    "class_local": "Dependent",
                    "reusable": False,
                    "reuse_scope": "occurrence_local",
                },
                {
                    "class_iri": NS + "Descriptor",
                    "class_local": "Descriptor",
                    "reusable": True,
                    "reuse_scope": "document",
                },
                {
                    "class_iri": NS + "NestedType",
                    "class_local": "NestedType",
                    "reusable": True,
                    "reuse_scope": "document",
                },
                {
                    "class_iri": NS + "Member",
                    "class_local": "Member",
                    "reusable": False,
                    "reuse_scope": "occurrence_local",
                },
                {
                    "class_iri": NS + "Container",
                    "class_local": "Container",
                    "reusable": False,
                    "reuse_scope": "never_by_generic_reuse",
                },
            ]
        },
    }
    return parsed, contract


def _bundle_all(candidates: dict) -> dict:
    decisions = []
    for item in candidates["candidates"]:
        kind = item["kind"]
        if kind == "leftover_public_linker":
            decision = "expose"
        elif kind == "container_membership":
            decision = "bundle"
        else:
            decision = "bundle"
        quotes = []
        evidence = list((item.get("tbox_evidence") or {}).values())
        if evidence and decision in {"bundle", "expose"}:
            quotes = [evidence[0][:24]]
        decisions.append(
            {
                "candidate_id": item["candidate_id"],
                "decision": decision,
                "evidence_quotes": quotes,
                "rationale": "test",
            }
        )
    return {
        "schema_version": "occurrence-surface-decisions.v1",
        "decisions": decisions,
    }


def test_discovers_optional_quantity_reusable_and_root_linker() -> None:
    parsed, contract = _fixture()
    discovered = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    kinds = {item["kind"] for item in discovered["candidates"]}
    assert "container_membership" in kinds
    assert "owner_quantity" in kinds
    assert "reusable_link" in kinds
    assert "fresh_dependent" in kinds
    assert "nested_reusable_link" in kinds
    assert "leftover_public_linker" in kinds
    assert "parent_link" in kinds
    parent_links = [
        item for item in discovered["candidates"] if item["kind"] == "parent_link"
    ]
    assert len(parent_links) == 1
    assert parent_links[0]["owner_class_local"] == "Dependent"
    assert parent_links[0]["predicate_local"] == "containsDependent"
    assert parent_links[0]["decision_space"] == "deterministic_bundle"
    assert parent_links[0]["structural_evidence"]["unique_incoming_from_top_entity"] is True


def test_compile_bundles_optional_facets_into_one_public_creator() -> None:
    parsed, contract = _fixture()
    candidates = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    contract["occurrence_surface_candidates"] = candidates
    contract["occurrence_surface_decisions"] = _bundle_all(candidates)
    compiled = compile_occurrence_surface(parsed=parsed, contract=contract)
    assert compiled["errors"] == []
    tools = {item["name"]: item for item in compiled["public_tools"]}
    assert "create_Member" in tools
    assert "create_Dependent" in tools
    assert "create_Descriptor" not in tools
    assert "create_Container" not in tools
    member = tools["create_Member"]
    assert member["parent_parameter"] == "parent_iri"
    assert member["idempotent"] is True
    assert any(item["parameter"] == "hasDuration" for item in member["quantities"])
    assert any(
        item["label_parameter"] == "ownsDependent_label"
        for item in member["fresh_dependents"]
    )
    assert any(
        item["label_parameter"] == "usesDescriptor_label"
        for item in member["reusable_links"]
    )
    assert any(
        item["label_parameter"] == "hasNestedType_label"
        for item in member["nested_reusable_links"]
    )
    linker_names = {item["name"] for item in compiled["public_linkers"]}
    assert linker_names == set()
    dependent = tools["create_Dependent"]
    assert dependent["parent_parameter"] == "parent_iri"
    assert dependent["parent_predicate_local"] == "containsDependent"
    assert dependent["parent_via_primitive"] is False
    assert dependent["idempotent"] is True
    assert dependent["identity_contract"]["kind"] == "semantic_occurrence"
    assert dependent["identity_contract"]["identity_args"][:2] == [
        "parent_iri",
        "label",
    ]


def test_generated_scripts_stay_contract_driven() -> None:
    parsed, contract = _fixture()
    candidates = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    contract["occurrence_surface_candidates"] = candidates
    contract["occurrence_surface_decisions"] = _bundle_all(candidates)
    compiled = compile_occurrence_surface(parsed=parsed, contract=contract)
    compiled["instruction"] = compile_fallback_instruction(compiled)
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="example"),
        contract={"occurrence_surface_units": compiled},
    )
    operations = emit_occurrence_operations(context, compiled)
    main = emit_occurrence_main(context, compiled)
    ownership = json.loads(emit_occurrence_argument_ownership(compiled))
    ast.parse(operations)
    ast.parse(main)
    assert "def create_Member(" in operations
    assert "ownsDependent_label" in operations
    assert "hasDuration" in operations
    assert "_resolve_or_create" in operations
    assert "_existing_fingerprint(" in operations
    assert "_existing_ordered_member(" in operations
    assert "def prepare_export_graph()" in operations
    assert "rdf_runtime.prepare_graph_for_export(" in operations
    assert "extra_keep_roots=extra_keep_roots" in operations
    assert "_MARKER_RESULT" in operations
    assert "def skip_semantic_obligation(" in operations
    assert "rdf_runtime.resolve_semantic_skip(obligation_id, reason)" in operations
    assert "rdf_runtime.register_semantic_rejection(" in operations
    assert "root_binding = rdf_runtime.bind_root_argument(parent_iri)" in operations
    assert "parent_iri = str(root_binding['effective_root_iri'])" in operations
    assert "allow_skip=True" not in operations.split("def create_Member(", 1)[1].split(
        "def ", 1
    )[0]
    assert "def create_Dependent(label: str, parent_iri: str" in operations
    assert "parent_iri must be the exact bound root IRI" in operations
    assert "Allowed arguments:" in operations
    assert "Nested ownership:" in operations
    assert "bare ontology property names" in operations
    assert "entities.create_Dependent(label=label" in operations
    assert "entities.create_Dependent(label=label, parent_iri=" not in operations
    assert "_link(relationships.add_containsDependent, parent_iri, owner_iri)" in operations
    assert "mcp.tool(name='create_Member')" in main
    assert "rdf_runtime.wrap_public_tool(operations.create_Member)" in main
    assert "mcp.tool(name=\"skip_semantic_obligation\")" in main
    assert "operations.prepare_export_graph()" in main
    assert 'exported["export_repairs"] = parsed' in main
    assert "mcp.tool(name='link_usesDescriptor')" not in main
    assert "add_usesDescriptor" not in main or "mcp.tool(name='add_" not in main
    assert "graph-only orphan pruning" in compiled["instruction"]
    assert compiled["instruction"].startswith("The pipeline has already called init_memory")
    assert "use the exact bound root IRI supplied by the pipeline" in compiled["instruction"]
    assert "nested ownership paths as authoritative" in compiled["instruction"]
    member_ownership = next(
        item for item in ownership["tools"] if item["name"] == "create_Member"
    )
    assert member_ownership["compatibility"]["nested_object_arguments"] is False
    assert (
        member_ownership["parameters"]["hasNestedType_label"]["owner_path"]
        == "self.usesDescriptor.hasNestedType"
    )


def test_instruction_rejects_invented_tools() -> None:
    parsed, contract = _fixture()
    candidates = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    contract["occurrence_surface_candidates"] = candidates
    contract["occurrence_surface_decisions"] = _bundle_all(candidates)
    compiled = compile_occurrence_surface(parsed=parsed, contract=contract)
    text = compile_fallback_instruction(compiled)
    assert mentioned_tool_names(text) <= public_tool_names(
        {**compiled, "instruction": text}
    )
    assert "export_memory" in mentioned_tool_names(text)


def test_infer_compiles_structurally_without_llm() -> None:
    parsed, contract = _fixture()

    def planner(_model: str, _prompt: str) -> dict:
        raise AssertionError("structural occurrence compile must not call the judge")

    context = SimpleNamespace(
        parsed=parsed,
        contract=contract,
        iteration_blueprint={},
    )
    infer_occurrence_surface(context, planner=planner, model="gpt-5")
    compiled = contract["occurrence_surface_units"]
    assert compiled["public_tools"]
    assert compiled["policy_source"] == "deterministic_tbox_occurrence_surface"
    assert contract["occurrence_surface_decisions"]["llm_judged_count"] == 0
    assert compiled["instruction"] == compile_fallback_instruction(compiled)
    assert "graph-only orphan pruning" in compiled["instruction"]
    assert "execution-point" not in compiled["instruction"]
    assert "parser-verified" in compiled["instruction"]
    assert contract["occurrence_surface_instruction"]["source"] == (
        "compiled_operational_instruction"
    )
    assert contract["materialization_operation_units"]["inference_mode"] in {
        "accepted_atomic",
        "legacy_split",
    }


def test_owner_local_facets_are_structurally_deterministic() -> None:
    parsed, contract = _fixture()
    discovered = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    residual = [
        item
        for item in discovered["candidates"]
        if not is_deterministic_candidate(item)
    ]
    assert residual == []
    kinds = {
        item["kind"]
        for item in discovered["candidates"]
        if item["kind"]
        in {
            "owner_quantity",
            "reusable_link",
            "fresh_dependent",
            "nested_reusable_link",
            "parent_link",
        }
    }
    assert kinds == {
        "owner_quantity",
        "reusable_link",
        "fresh_dependent",
        "nested_reusable_link",
        "parent_link",
    }
    assert all(
        item["decision_space"] == "deterministic_bundle"
        for item in discovered["candidates"]
        if item["kind"] in kinds
    )


def test_quote_flakes_do_not_discard_accepted_decisions() -> None:
    parsed, contract = _fixture()
    discovered = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    judged = {
        "schema_version": discovered["schema_version"],
        "candidates": [
            item
            for item in discovered["candidates"]
            if item["kind"] in {"owner_quantity", "reusable_link"}
        ],
    }
    raw = _bundle_all(judged)
    raw["decisions"][0]["evidence_quotes"] = []
    raw["decisions"][1]["evidence_quotes"] = ["not a verbatim excerpt"]
    bundle, errors = _validate_decisions(judged, raw)
    assert errors == []
    repaired = set(bundle.get("quote_repairs") or [])
    assert raw["decisions"][0]["candidate_id"] in repaired
    assert raw["decisions"][1]["candidate_id"] in repaired
    by_id = {item["candidate_id"]: item for item in judged["candidates"]}
    for item in bundle["decisions"][:2]:
        evidence = list((by_id[item["candidate_id"]].get("tbox_evidence") or {}).values())
        assert item["evidence_quotes"]
        assert any(item["evidence_quotes"][0] in text for text in evidence)


def test_illegal_item_fallback_keeps_other_decisions() -> None:
    parsed, contract = _fixture()
    discovered = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    residual = [
        {**item, "decision_space": "llm"}
        for item in discovered["candidates"]
        if item["kind"] in {"owner_quantity", "reusable_link"}
    ]
    broken_id = next(
        item["candidate_id"] for item in residual if item["kind"] == "owner_quantity"
    )

    def planner(_model: str, _prompt: str) -> dict:
        payload = _bundle_all({"candidates": residual})
        for item in payload["decisions"]:
            if item["candidate_id"] == broken_id:
                item["decision"] = "merge"
        return payload

    judged, errors, _ = _judge_missing(
        planner,
        "gpt-5",
        residual,
        {
            "schema_version": discovered["schema_version"],
            "candidates": residual,
            "selection_policy": "",
        },
    )
    by_id = {item["candidate_id"]: item for item in judged}
    assert by_id[broken_id]["decision"] == "separate"
    assert errors
    assert sum(item["decision"] == "bundle" for item in judged) >= 1


def test_missing_quotes_do_not_wipe_the_compiled_surface() -> None:
    parsed, contract = _fixture()

    def planner(_model: str, _prompt: str) -> dict:
        raise AssertionError("structural compile must not call the judge")

    context = SimpleNamespace(
        parsed=parsed,
        contract=contract,
        iteration_blueprint={},
    )
    infer_occurrence_surface(context, planner=planner, model="gpt-5")
    compiled = context.contract["occurrence_surface_units"]
    tools = {item["name"]: item for item in compiled["public_tools"]}
    assert context.contract["occurrence_surface_decisions"].get("fallback") is None
    assert any(item["parameter"] == "hasDuration" for item in tools["create_Member"]["quantities"])
    assert tools["create_Member"]["reusable_links"]
    assert tools["create_Member"]["fresh_dependents"]


def test_wiped_fallback_checkpoint_cannot_strip_structural_facets(tmp_path: Path) -> None:
    parsed, contract = _fixture()
    checkpoint = tmp_path / "decisions.json"
    context = SimpleNamespace(
        parsed=parsed,
        contract=contract,
        iteration_blueprint={},
    )

    def planner(_model: str, _prompt: str) -> dict:
        raise AssertionError("structural compile must not call the judge")

    infer_occurrence_surface(
        context,
        planner=planner,
        model="gpt-5",
        checkpoint_path=checkpoint,
    )
    cached = json.loads(checkpoint.read_text(encoding="utf-8"))
    cached["fallback"] = "separate_optional_facets"
    for item in cached["decisions"]:
        if item["decision"] == "bundle":
            item["decision"] = "separate"
            item["rationale"] = "Fail-closed fallback after invalid semantic judgement."
    checkpoint.write_text(json.dumps(cached), encoding="utf-8")
    infer_occurrence_surface(
        context,
        planner=planner,
        model="gpt-5",
        checkpoint_path=checkpoint,
    )
    assert context.contract["occurrence_surface_decisions"].get("fallback") is None
    tools = {
        item["name"]: item
        for item in context.contract["occurrence_surface_units"]["public_tools"]
    }
    assert tools["create_Member"]["quantities"]


def test_infer_reuses_prior_judged_decisions_without_llm() -> None:
    parsed, contract = _fixture()
    discovered = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    judged = {
        "candidates": [
            item
            for item in discovered["candidates"]
            if not is_deterministic_candidate(item)
        ]
    }

    def planner(_model: str, _prompt: str) -> dict:
        raise AssertionError("planner must not be called when prior decisions cover judged candidates")

    context = SimpleNamespace(
        parsed=parsed,
        contract=contract,
        iteration_blueprint={},
    )
    infer_occurrence_surface(
        context,
        planner=planner,
        model="gpt-5",
        prior_decisions=_bundle_all(judged),
    )
    compiled = contract["occurrence_surface_units"]
    tools = {item["name"]: item for item in compiled["public_tools"]}
    assert tools["create_Dependent"]["parent_parameter"] == "parent_iri"
    assert contract["occurrence_surface_decisions"]["llm_judged_count"] == 0


OM2_YIELD = "http://www.ontology-of-units-of-measure.org/resource/om-2/AmountOfSubstanceFraction"


def _capability_fixture() -> tuple[dict, dict]:
    parsed, contract = _fixture()
    parsed["classes"].update(
        {
            "Output": {
                "iri": NS + "Output",
                "parent_classes": [],
                "comment": "Unique parent-owned product of the root container.",
            },
            "Representation": {
                "iri": NS + "Representation",
                "parent_classes": [],
                "comment": "Reusable identity of an output, keyed by a stable code.",
            },
            "Device": {
                "iri": NS + "Device",
                "parent_classes": [],
                "comment": "Reusable device attached to the root.",
            },
            "Instrument": {
                "iri": NS + "Instrument",
                "parent_classes": ["Device"],
                "comment": "More specific reusable instrument subclass.",
            },
            "BannedStep": {
                "iri": NS + "BannedStep",
                "parent_classes": [],
                "comment": "Ordered occurrence that reuse policy prohibits creating.",
            },
        }
    )
    parsed["properties"].update(
        {
            "containsOutput": {
                "comment": "Exactly one output belongs to the root container."
            },
            "isRepresentedBy": {
                "comment": "Output identity represented by a reusable coded object."
            },
            "hasCode": {"comment": "Stable identity code of a representation."},
            "hasYield": {"comment": "Optional yield quantity of the root container."},
            "hasDevice": {
                "comment": "Reusable exact-range device of the already-created root."
            },
            "usesInstrument": {
                "comment": "Optional reusable instrument of an ordered heading."
            },
        }
    )
    publish = contract["ontology_publish_contract"]
    publish["subclass_closure"].extend(
        [
            {"class_iri": NS + "Output", "superclass_iris": []},
            {"class_iri": NS + "Representation", "superclass_iris": []},
            {"class_iri": NS + "Device", "superclass_iris": []},
            {
                "class_iri": NS + "Instrument",
                "superclass_iris": [NS + "Instrument", NS + "Device"],
            },
            {"class_iri": NS + "BannedStep", "superclass_iris": []},
        ]
    )
    publish["datatype_properties"].append(
        {
            "property_iri": NS + "hasCode",
            "domain_iris": [NS + "Representation"],
            "range_iris": [XSD + "string"],
        }
    )
    profile = contract["ordered_member_profile"]
    profile["ordered_member_classes"] = ["Member", "BannedStep"]
    profile["individually_linked_object_properties"] = ["containsMember"]
    relationships = contract["relationship_tool_contracts"]
    relationships["containsMember"]["range_iris"] = [NS + "Member", NS + "BannedStep"]
    relationships.update(
        {
            "containsOutput": {
                "predicate_local": "containsOutput",
                "predicate_iri": NS + "containsOutput",
                "domain_iris": [NS + "Container"],
                "range_iris": [NS + "Output"],
            },
            "isRepresentedBy": {
                "predicate_local": "isRepresentedBy",
                "predicate_iri": NS + "isRepresentedBy",
                "domain_iris": [NS + "Output"],
                "range_iris": [NS + "Representation"],
            },
            "hasYield": {
                "predicate_local": "hasYield",
                "predicate_iri": NS + "hasYield",
                "domain_iris": [NS + "Container"],
                "range_iris": [OM2_YIELD],
                "fixed_runtime_range_iris": [OM2_YIELD],
            },
            "hasDevice": {
                "predicate_local": "hasDevice",
                "predicate_iri": NS + "hasDevice",
                "domain_iris": [NS + "Container"],
                "range_iris": [NS + "Device"],
            },
            "usesInstrument": {
                "predicate_local": "usesInstrument",
                "predicate_iri": NS + "usesInstrument",
                "domain_iris": [NS + "Member", NS + "BannedStep"],
                "range_iris": [NS + "Instrument"],
            },
        }
    )
    contract["reuse_policy"]["classes"].extend(
        [
            {
                "class_iri": NS + "Output",
                "class_local": "Output",
                "reusable": False,
                "reuse_scope": "occurrence_local",
            },
            {
                "class_iri": NS + "Representation",
                "class_local": "Representation",
                "reusable": True,
                "reuse_scope": "document",
            },
            {
                "class_iri": NS + "Device",
                "class_local": "Device",
                "reusable": True,
                "reuse_scope": "document",
            },
            {
                "class_iri": NS + "Instrument",
                "class_local": "Instrument",
                "reusable": True,
                "reuse_scope": "document",
            },
            {
                "class_iri": NS + "BannedStep",
                "class_local": "BannedStep",
                "reusable": False,
                "reuse_scope": "prohibited",
            },
        ]
    )
    contract["occurrence_surface_include_prohibited_ordered"] = True
    return parsed, contract


def _compile_capability() -> dict:
    parsed, contract = _capability_fixture()
    candidates = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    contract["occurrence_surface_candidates"] = candidates
    contract["occurrence_surface_decisions"] = _bundle_all(candidates)
    return compile_occurrence_surface(parsed=parsed, contract=contract)


def test_compile_parent_quantity_identity_and_exact_range_leftover() -> None:
    parsed, contract = _capability_fixture()
    discovered = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    kinds = {item["kind"] for item in discovered["candidates"]}
    assert "leftover_root_quantity" in kinds
    assert "leftover_public_linker" in kinds
    leftover = [
        item
        for item in discovered["candidates"]
        if item["kind"] == "leftover_public_linker"
    ]
    leftover_predicates = {item["predicate_local"] for item in leftover}
    assert "hasDevice" in leftover_predicates
    assert any(item["target_class_local"] == "Device" for item in leftover)
    compiled = _compile_capability()
    assert compiled["errors"] == []
    tools = {item["name"]: item for item in compiled["public_tools"]}
    assert "create_Output" in tools
    assert "create_BannedStep" in tools
    assert "create_Representation" not in tools
    output = tools["create_Output"]
    assert output["parent_parameter"] == "parent_iri"
    assert output["parent_unique_incoming"] is True
    assert output["idempotent"] is True
    assert output["parent_predicate_iri"] == NS + "containsOutput"
    assert any(item["parameter"] == "hasYield" for item in output["parent_quantities"])
    represented = next(
        item
        for item in output["reusable_links"]
        if item["predicate_local"] == "isRepresentedBy"
    )
    assert represented["create_fresh_with_datatypes"] is True
    assert represented["default_label_from_owner"] is True
    assert any(
        item["parameter_name"] == "isRepresentedBy_hasCode"
        for item in represented["datatype_inputs"]
    )
    linker_names = {item["name"] for item in compiled["public_linkers"]}
    assert "link_hasDevice" in linker_names
    assert "link_hasYield" not in linker_names
    assert "link_usesInstrument" not in linker_names
    banned = tools["create_BannedStep"]
    assert banned["ordered_member"] is True
    assert banned["idempotent"] is True
    guard = compiled["loop_guard"]
    assert guard == compile_loop_guard_contract(compiled)
    unique_names = {item["name"] for item in guard["unique_parent_tools"]}
    ordered_names = {item["name"] for item in guard["ordered_member_tools"]}
    assert unique_names == {"create_Output"}
    assert guard["unique_parent_tools"][0]["identity_args"] == ["parent_iri"]
    assert "create_Member" in ordered_names
    assert "create_BannedStep" in ordered_names
    assert "create_Dependent" not in unique_names
    assert json.loads(emit_occurrence_loop_guard(compiled))["unique_parent_tools"]


def test_generated_output_uses_graph_idempotency_and_fresh_identity() -> None:
    compiled = _compile_capability()
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="example"),
        contract={"occurrence_surface_units": compiled},
    )
    operations = emit_occurrence_operations(context, compiled)
    ast.parse(operations)
    assert "_existing_parent_member(parent_iri," in operations
    assert "def check_ordered_members()" in operations
    assert "TWA_MCP_EXPECTED_ORDERED_MEMBERS_JSON" not in operations
    assert "RDF.type, URIRef(class_iri)" in operations
    assert "if hasYield:" in operations
    assert "_attach_quantity(parent_iri, 'hasYield'" in operations
    assert "isRepresentedBy_hasCode" in operations
    assert "isRepresentedBy_label = isRepresentedBy_label or _optional_label(label)" in operations
    assert "ensure=_ensure_default_links" in operations


def test_declared_bridge_class_makes_representation_label_required() -> None:
    parsed, contract = _capability_fixture()
    contract["extension_bridge_class_iris"] = [NS + "Representation"]
    candidates = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    contract["occurrence_surface_candidates"] = candidates
    contract["occurrence_surface_decisions"] = _bundle_all(candidates)
    compiled = compile_occurrence_surface(parsed=parsed, contract=contract)
    output = next(
        item for item in compiled["public_tools"] if item["name"] == "create_Output"
    )
    represented = next(
        item
        for item in output["reusable_links"]
        if item["predicate_local"] == "isRepresentedBy"
    )
    assert represented["required_bridge_link"] is True
    assert represented["default_label_from_owner"] is True
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="example"),
        contract={"occurrence_surface_units": compiled},
    )
    operations = emit_occurrence_operations(context, compiled)
    assert (
        "isRepresentedBy_label: str," in operations
        or "isRepresentedBy_label: str *" in operations.replace(",", " ")
    )
    assert "isRepresentedBy_label: str | None = None" not in operations
    assert "Required representation labels compiled from declared extension bridge" in (
        compiled["instruction"]
    )
    assert collect_extension_bridge_class_iris(
        runtime={
            "extensions": [
                {"name": "demo", "bridge_class_iri": NS + "Representation"}
            ]
        }
    ) == [NS + "Representation"]
    assert "if isRepresentedBy_label:" in operations
    assert "entities.create_Representation(label=isRepresentedBy_label" in operations
    output_body = operations.split("def create_Output(", 1)[1].split("\ndef ", 1)[0]
    member_body = operations.split("def create_Member(", 1)[1].split("\ndef ", 1)[0]
    assert "or _optional_label(label)" in output_body
    assert "usesInstrument_label = usesInstrument_label or _optional_label(label)" not in member_body
    assert "def create_BannedStep(" in operations
    assert "def link_hasDevice(" in operations


def test_infer_inherits_ordered_sibling_decisions_without_llm() -> None:
    parsed, contract = _capability_fixture()
    discovered = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    member_judged = [
        item
        for item in discovered["candidates"]
        if not is_deterministic_candidate(item)
        and item.get("owner_class_local") != "BannedStep"
    ]

    def planner(_model: str, _prompt: str) -> dict:
        raise AssertionError("sibling inheritance must not call the judge")

    context = SimpleNamespace(
        parsed=parsed,
        contract=contract,
        iteration_blueprint={},
    )
    infer_occurrence_surface(
        context,
        planner=planner,
        model="gpt-5",
        prior_decisions=_bundle_all({"candidates": member_judged}),
    )
    compiled = context.contract["occurrence_surface_units"]
    tools = {item["name"]: item for item in compiled["public_tools"]}
    assert "create_BannedStep" in tools
    assert context.contract["occurrence_surface_decisions"]["llm_judged_count"] == 0


def test_generator_modules_contain_no_ontology_literals() -> None:
    root = Path("src/agents/scripts_and_prompts_generation")
    forbidden = (
        "OntoSyn",
        "OntoSynthesis",
        "ontosyn:",
        "theworldavatar.com/kg/OntoSyn",
        "ChemicalInput",
        "ChemicalOutput",
        "hasWashingSolvent",
        "HeatChill",
        "create_add_step",
        "VesselEnvironment",
        "hasVessel",
        "isSuppliedBy",
        "ExecutionPoint",
        "execution-point",
        "Sigma-Aldrich",
        "mother liquor",
        "Container",
        "Member",
        "Dependent",
        "Descriptor",
        "Material",
        "chemical",
    )
    for name in (
        "occurrence_surface_units.py",
        "occurrence_surface_inference.py",
        "occurrence_surface_scripts.py",
        "materialization_operation_units.py",
        "materialization_operation_inference.py",
        "reuse_policy.py",
    ):
        text = (root / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{name} contains {token}"


def _public_surface(compiled: dict) -> dict[str, object]:
    tools = {}
    for item in compiled.get("public_tools") or []:
        names = ["label"]
        if item.get("parent_parameter"):
            names.append(str(item["parent_parameter"]))
        if item.get("ordering_property_local"):
            names.append(str(item["ordering_property_local"]))
        names.extend(
            str(value.get("property_local") or "")
            for value in item.get("datatype_inputs") or []
        )
        names.extend(str(value.get("parameter") or "") for value in item.get("quantities") or [])
        for group in ("fresh_dependents", "reusable_links"):
            for value in item.get(group) or []:
                names.append(str(value.get("label_parameter") or ""))
                names.extend(
                    str(nested.get("parameter_name") or "")
                    for nested in value.get("datatype_inputs") or []
                )
        names.extend(
            str(value.get("label_parameter") or "")
            for value in item.get("nested_reusable_links") or []
        )
        tools[str(item.get("name") or "")] = [name for name in names if name]
    return {
        "tools": tools,
        "linkers": sorted(
            str(item.get("name") or "")
            for item in compiled.get("public_linkers") or []
            if str(item.get("name") or "")
        ),
    }


def test_repeated_infer_emits_identical_public_surface() -> None:
    parsed, contract = _fixture()

    def planner(_model: str, _prompt: str) -> dict:
        raise AssertionError("structural compile must not call the judge")

    surfaces = []
    for _ in range(3):
        copy = json.loads(json.dumps(contract))
        context = SimpleNamespace(
            parsed=parsed,
            contract=copy,
            iteration_blueprint={},
        )
        infer_occurrence_surface(context, planner=planner, model="gpt-5")
        surfaces.append(_public_surface(copy["occurrence_surface_units"]))
    assert surfaces[0] == surfaces[1] == surfaces[2]


def test_frozen_tbox_compile_matches_indep10_public_surface() -> None:
    source = Path("ai_generated_contents_inferred_atomic_context_20260831")
    baseline = Path("ai_generated_contents_occurrence_surface_20260902_indep10")
    parsed_path = source / "ontology_structures" / "ontosynthesis" / "parsed.json"
    contract_path = (
        source / "ontology_structures" / "ontosynthesis" / "generation_contract.json"
    )
    baseline_path = (
        baseline
        / "ontology_structures"
        / "ontosynthesis"
        / "generation_contract.json"
    )
    if not parsed_path.is_file() or not contract_path.is_file() or not baseline_path.is_file():
        return
    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    for key in (
        "materialization_operation_candidates",
        "materialization_operation_decisions",
        "materialization_operation_units",
        "occurrence_surface_candidates",
        "occurrence_surface_decisions",
        "occurrence_surface_units",
        "occurrence_surface_instruction",
    ):
        contract.pop(key, None)

    def planner(_model: str, _prompt: str) -> dict:
        raise AssertionError("frozen T-Box compile must not call the judge")

    context = SimpleNamespace(
        parsed=parsed,
        contract=contract,
        iteration_blueprint={},
    )
    infer_occurrence_surface(context, planner=planner, model="gpt-5")
    compiled = contract["occurrence_surface_units"]
    baseline_compiled = json.loads(baseline_path.read_text(encoding="utf-8"))[
        "occurrence_surface_units"
    ]
    assert contract["occurrence_surface_decisions"]["llm_judged_count"] == 0
    assert _public_surface(compiled) == _public_surface(baseline_compiled)
    compiled_tools = {item["name"]: item for item in compiled["public_tools"]}
    baseline_tools = {item["name"]: item for item in baseline_compiled["public_tools"]}
    assert {
        name: item.get("identity_contract") for name, item in compiled_tools.items()
    } == {
        name: item.get("identity_contract") for name, item in baseline_tools.items()
    }
    assert compiled_tools["create_ChemicalOutput"]["identity_contract"] == baseline_tools[
        "create_ChemicalOutput"
    ]["identity_contract"]
    assert compiled_tools["create_ChemicalOutput"]["parent_unique_incoming"] is True
    assert compiled_tools["create_ChemicalOutput"]["parent_binds_to_session_root"] is True
    assert compiled_tools["create_ChemicalInput"]["identity_contract"]["kind"] == (
        "semantic_occurrence"
    )
    assert "never pass a created child handle" in compiled["instruction"]
    assert "do not substitute the bound root" not in compiled["instruction"]


def test_example_fixture_compiles_all_mutation_identity_contracts() -> None:
    compiled = _compile_capability()
    tools = {item["name"]: item for item in compiled["public_tools"]}
    assert tools["create_Member"]["identity_contract"] == {
        "kind": "ordered",
        "identity_args": ["parent_iri", "orderValue"],
    }
    assert tools["create_Output"]["identity_contract"] == {
        "kind": "unique_parent",
        "identity_args": ["parent_iri"],
    }
    dependent_identity = tools["create_Dependent"]["identity_contract"]
    assert dependent_identity["kind"] == "semantic_occurrence"
    assert dependent_identity["identity_args"] == ["parent_iri", "label"]
    linkers = {item["name"]: item for item in compiled["public_linkers"]}
    assert linkers["link_hasDevice"]["identity_contract"] == {
        "kind": "semantic_link",
        "identity_args": ["subject_iri", "object_label"],
    }
    guarded = {item["name"]: item for item in compiled["loop_guard"]["mutation_tools"]}
    assert guarded.keys() == {
        *(item["name"] for item in compiled["public_tools"]),
        *(item["name"] for item in compiled["public_linkers"]),
    }


def test_example_fixture_emits_persistent_replay_and_facet_receipts() -> None:
    compiled = _compile_capability()
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="example"),
        contract={"occurrence_surface_units": compiled},
    )
    operations = emit_occurrence_operations(context, compiled)
    ast.parse(operations)
    assert "_COMMITTED" not in operations
    assert '_MARKER_BASE = "urn:twa:semantic-mutation:"' in operations
    assert "semantic_fingerprint=fingerprint" in operations
    assert "already_committed=True" in operations
    assert "graph_changed=False" in operations
    assert "graph_revision=_graph_revision()" in operations
    assert "facet_warnings=facet_warnings" in operations
    assert "_try_attach_quantity(" in operations
    assert 'str(payload.get("code") or "") == "INVALID_OM2_QUANTITY"' in operations
    assert '"source_value": str(label)' in operations
    assert "register_semantic_rejection(obligation_id, warning" in operations
    assert '_ABSENT_LABELS = frozenset({"", "n/a", "na", "unknown", "not specified"})' in operations
    assert "object_label = _optional_label(object_label)" in operations


def test_generated_ordered_check_does_not_consume_pipeline_ledger_context(
    monkeypatch,
) -> None:
    root_iri = "urn:test:root"
    class_iri = "urn:test:Step"
    wrong_class_iri = "urn:test:WrongStep"
    membership_iri = "urn:test:hasStep"
    order_iri = "urn:test:order"
    compiled = {
        "public_tools": [
            {
                "name": "create_Step",
                "primitive_tool": "create_Step",
                "owner_class_local": "Step",
                "owner_class_iri": class_iri,
                "ordered_member": True,
                "ordering_property_local": "order",
                "ordering_property_iri": order_iri,
                "parent_parameter": "parent_iri",
                "parent_predicate_local": "hasStep",
                "parent_predicate_iri": membership_iri,
                "parent_via_primitive": True,
                "datatype_inputs": [],
                "quantities": [],
                "parent_quantities": [],
                "fresh_dependents": [],
                "reusable_links": [],
                "nested_reusable_links": [],
                "identity_contract": {
                    "kind": "ordered",
                    "identity_args": ["parent_iri", "order"],
                },
            }
        ],
        "public_linkers": [],
        "reusable_classes": [],
    }
    graph = Graph()

    def success_json(**metadata: object) -> str:
        return json.dumps({"status": "ok", **metadata}, sort_keys=True)

    runtime = SimpleNamespace(
        retained_graph=lambda: graph,
        bound_root_iri=lambda: root_iri,
        success_json=success_json,
        error_json=lambda **metadata: json.dumps(
            {"status": "rejected", **metadata}, sort_keys=True
        ),
    )
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="example"),
        contract={"occurrence_surface_units": compiled},
    )
    tree = ast.parse(emit_occurrence_operations(context, compiled))
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, ast.ImportFrom) or node.level == 0
    ]
    namespace = {
        "rdf_runtime": runtime,
        "entities": SimpleNamespace(),
        "relationships": SimpleNamespace(),
    }
    exec(compile(tree, "<coverage-gate>", "exec"), namespace)
    monkeypatch.setenv(
        "TWA_MCP_EXPECTED_ORDERED_MEMBERS_JSON",
        json.dumps([{"class_local": "Step", "order": 1}]),
    )

    missing = json.loads(namespace["check_ordered_members"]())
    assert missing["status"] == "ok"
    assert missing["violations"] == []

    wrong = URIRef("urn:test:wrong")
    graph.add((URIRef(root_iri), URIRef(membership_iri), wrong))
    graph.add((wrong, RDF.type, URIRef(wrong_class_iri)))
    graph.add((wrong, URIRef(order_iri), Literal(1)))
    still_missing = json.loads(namespace["check_ordered_members"]())
    assert still_missing["status"] == "ok"
    assert still_missing["violations"] == []

    correct = URIRef("urn:test:correct")
    graph.add((URIRef(root_iri), URIRef(membership_iri), correct))
    graph.add((correct, RDF.type, URIRef(class_iri)))
    graph.add((correct, URIRef(order_iri), Literal(1)))
    complete = json.loads(namespace["check_ordered_members"]())
    assert complete["status"] == "ok"
    assert complete["violations"] == []


def test_generated_example_runtime_replays_and_keeps_owner_on_bad_facet() -> None:
    class_iri = NS + "Record"
    reusable_iri = NS + "Reference"
    quantity_iri = NS + "Measure"
    predicate_iri = NS + "hasMeasure"
    link_predicate_iri = NS + "hasReference"
    child_iri = NS + "Child"
    child_predicate_iri = NS + "hasChild"
    compiled = {
        "public_tools": [
            {
                "name": "create_Record",
                "primitive_tool": "create_Record",
                "owner_class_iri": class_iri,
                "ordering_property_local": "",
                "parent_parameter": "",
                "datatype_inputs": [],
                "quantities": [
                    {
                        "parameter": "hasMeasure",
                        "predicate_local": "hasMeasure",
                        "predicate_iri": predicate_iri,
                        "range_iri": quantity_iri,
                    }
                ],
                "parent_quantities": [],
                "fresh_dependents": [
                    {
                        "label_parameter": "hasChild_label",
                        "predicate_local": "hasChild",
                        "predicate_iri": child_predicate_iri,
                        "target_class_local": "Child",
                        "target_class_iri": child_iri,
                        "create_tool": "create_Child",
                        "datatype_inputs": [],
                    }
                ],
                "reusable_links": [],
                "nested_reusable_links": [],
                "identity_contract": {
                    "kind": "semantic_occurrence",
                    "identity_args": ["label"],
                },
            }
        ],
        "public_linkers": [
            {
                "name": "link_hasReference",
                "predicate_local": "hasReference",
                "predicate_iri": link_predicate_iri,
                "object_class_local": "Reference",
                "object_class_iri": reusable_iri,
                "identity_contract": {
                    "kind": "semantic_link",
                    "identity_args": ["subject_iri", "object_label"],
                },
            }
        ],
        "reusable_classes": [
            {
                "class_local": "Reference",
                "class_iri": reusable_iri,
                "create_tool": "create_Reference",
            }
        ],
    }
    graph = Graph()
    minted = 0

    def success_json(*, iri: str = "", message: str = "", **metadata: object) -> str:
        return json.dumps(
            {"status": "ok", "iri": iri, "message": message, **metadata},
            sort_keys=True,
        )

    def error_json(*, code: str, message: str, **metadata: object) -> str:
        return json.dumps(
            {"status": "rejected", "code": code, "message": message, **metadata},
            sort_keys=True,
        )

    def create(class_value: str, label: str) -> str:
        nonlocal minted
        minted += 1
        iri = f"urn:test:{minted}"
        graph.add((URIRef(iri), RDF.type, URIRef(class_value)))
        graph.add((URIRef(iri), RDFS.label, Literal(label)))
        return success_json(iri=iri)

    def add(predicate: str, subject: str, obj: str) -> str:
        graph.add((URIRef(subject), URIRef(predicate), URIRef(obj)))
        return success_json(iri=subject)

    @contextmanager
    def transaction():
        before = set(graph)
        try:
            yield graph
        except BaseException:
            graph.remove((None, None, None))
            for triple in before:
                graph.add(triple)
            raise

    runtime = SimpleNamespace(
        retained_graph=lambda: graph,
        atomic_graph_transaction=transaction,
        success_json=success_json,
        error_json=error_json,
        bind_root_argument=lambda requested: {
            "requested_root_iri": requested,
            "effective_root_iri": requested,
            "root_argument_canonicalized": False,
            "binding_source": "legacy_argument",
        },
        register_semantic_rejection=lambda *_args, **_kwargs: None,
        resolve_semantic_skip=lambda obligation_id, reason: error_json(
            code="UNKNOWN_SEMANTIC_OBLIGATION",
            message="not registered",
            obligation_id=obligation_id,
            reason=reason,
        ),
        create_om2_quantity=lambda *_args: error_json(
            code="INVALID_QUANTITY", message="unsupported test facet"
        ),
    )
    entities = SimpleNamespace(
        create_Record=lambda label: create(class_iri, label),
        create_Reference=lambda label: create(reusable_iri, label),
        create_Child=lambda label: create(child_iri, label),
    )
    relationships = SimpleNamespace(
        add_hasMeasure=lambda subject, obj: add(predicate_iri, subject, obj),
        add_hasReference=lambda subject, obj: add(link_predicate_iri, subject, obj),
        add_hasChild=lambda subject, obj: add(child_predicate_iri, subject, obj),
    )
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="example"),
        contract={"occurrence_surface_units": compiled},
    )
    tree = ast.parse(emit_occurrence_operations(context, compiled))
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, ast.ImportFrom) or node.level == 0
    ]
    namespace = {
        "rdf_runtime": runtime,
        "entities": entities,
        "relationships": relationships,
    }
    exec(compile(tree, "<generated-occurrence>", "exec"), namespace)

    created = json.loads(namespace["create_Record"]("sample", hasMeasure="bad"))
    assert created["status"] == "ok"
    assert created["graph_changed"] is True
    assert created["already_committed"] is False
    assert created["graph_revision"] == 1
    assert created["omitted_facet"] is True
    assert created["facet_warnings"][0]["facet"] == "hasMeasure"
    assert (URIRef(created["iri"]), RDF.type, URIRef(class_iri)) in graph

    replayed = json.loads(namespace["create_Record"](" SAMPLE ", hasMeasure=" bad "))
    assert replayed["iri"] == created["iri"]
    assert replayed["already_committed"] is True
    assert replayed["graph_changed"] is False
    assert replayed["graph_revision"] == 1

    child = json.loads(
        namespace["create_Record"]("multi", hasChild_label="DMF")
    )
    multi_owner = URIRef(child["iri"])
    assert len(list(graph.objects(multi_owner, URIRef(child_predicate_iri)))) == 1
    replayed_child = json.loads(
        namespace["create_Record"]("multi", hasChild_label="acetone")
    )
    assert replayed_child["iri"] == child["iri"]
    assert replayed_child["already_committed"] is True
    assert replayed_child["graph_changed"] is False
    assert len(list(graph.objects(multi_owner, URIRef(child_predicate_iri)))) == 1

    linked = json.loads(namespace["link_hasReference"](created["iri"], "alpha"))
    assert linked["graph_changed"] is True
    assert linked["graph_revision"] == 3
    replayed_link = json.loads(
        namespace["link_hasReference"](created["iri"], "  ALPHA ")
    )
    assert replayed_link["already_committed"] is True
    assert replayed_link["graph_changed"] is False
    assert replayed_link["graph_revision"] == 3

    restarted_namespace = {
        "rdf_runtime": runtime,
        "entities": entities,
        "relationships": relationships,
    }
    exec(compile(tree, "<restarted-generated-occurrence>", "exec"), restarted_namespace)
    after_restart = json.loads(
        restarted_namespace["create_Record"]("sample", hasMeasure="bad")
    )
    assert after_restart["iri"] == created["iri"]
    assert after_restart["already_committed"] is True
    assert after_restart["graph_changed"] is False

    before_sentinel = set(graph)
    sentinel = json.loads(namespace["link_hasReference"](created["iri"], "unknown"))
    assert sentinel["status"] == "rejected"
    assert sentinel["already_committed"] is False
    assert sentinel["graph_changed"] is False
    assert set(graph) == before_sentinel


def _non_top_child_fixture() -> tuple[dict, dict]:
    parsed, contract = _fixture()
    parsed["classes"]["Cluster"] = {
        "iri": NS + "Cluster",
        "parent_classes": [],
        "comment": "Child owned only by a non-root heading.",
    }
    parsed["properties"]["ownsCluster"] = {
        "comment": "A heading may own many clusters."
    }
    contract["ontology_publish_contract"]["subclass_closure"].append(
        {"class_iri": NS + "Cluster", "superclass_iris": []}
    )
    contract["ontology_publish_contract"]["datatype_properties"].append(
        {
            "property_iri": NS + "clusterCode",
            "domain_iris": [NS + "Cluster"],
            "range_iris": [XSD + "string"],
        }
    )
    parsed["properties"]["clusterCode"] = {"comment": "Optional code on a cluster."}
    contract["relationship_tool_contracts"]["ownsCluster"] = {
        "predicate_local": "ownsCluster",
        "predicate_iri": NS + "ownsCluster",
        "domain_iris": [NS + "Member"],
        "range_iris": [NS + "Cluster"],
    }
    contract["reuse_policy"]["classes"].append(
        {
            "class_iri": NS + "Cluster",
            "class_local": "Cluster",
            "reusable": False,
            "reuse_scope": "occurrence_local",
        }
    )
    return parsed, contract


def test_non_top_unique_incoming_is_semantic_occurrence_and_skips_root_bind() -> None:
    parsed, contract = _non_top_child_fixture()
    candidates = discover_occurrence_surface_candidates(
        parsed=parsed, contract=contract
    )
    contract["occurrence_surface_candidates"] = candidates
    contract["occurrence_surface_decisions"] = _bundle_all(candidates)
    compiled = compile_occurrence_surface(parsed=parsed, contract=contract)
    tools = {item["name"]: item for item in compiled["public_tools"]}
    cluster = tools["create_Cluster"]
    assert cluster["parent_unique_incoming"] is False
    assert cluster["parent_binds_to_session_root"] is False
    assert cluster["identity_contract"] == {
        "kind": "semantic_occurrence",
        "identity_args": ["parent_iri", "label"],
    }
    assert "do not substitute the bound root" in compiled["instruction"]
    fixture_compiled = compile_occurrence_surface(
        parsed=_fixture()[0],
        contract={
            **_fixture()[1],
            "occurrence_surface_candidates": discover_occurrence_surface_candidates(
                parsed=_fixture()[0], contract=_fixture()[1]
            ),
            "occurrence_surface_decisions": _bundle_all(
                discover_occurrence_surface_candidates(
                    parsed=_fixture()[0], contract=_fixture()[1]
                )
            ),
        },
    )
    assert "never pass a created child handle" in fixture_compiled["instruction"]
    assert "do not substitute the bound root" not in fixture_compiled["instruction"]
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="example"),
        contract={"occurrence_surface_units": compiled},
    )
    operations = emit_occurrence_operations(context, compiled)
    cluster_body = operations.split("def create_Cluster(", 1)[1].split("\ndef ", 1)[0]
    assert "bind_root_argument(parent_iri)" not in cluster_body
    assert "bind_parent_occurrence_argument(parent_iri)" in cluster_body
    assert "parent occurrence that owns this child" in cluster_body
    member_body = operations.split("def create_Member(", 1)[1].split("\ndef ", 1)[0]
    assert "bind_root_argument(parent_iri)" in member_body


def test_unique_parent_reuse_fills_missing_datatypes() -> None:
    root_iri = "urn:test:root"
    class_iri = NS + "Sheet"
    parent_predicate_iri = NS + "hasSheet"
    name_iri = NS + "sheetName"
    compiled = {
        "public_tools": [
            {
                "name": "create_Sheet",
                "primitive_tool": "create_Sheet",
                "owner_class_iri": class_iri,
                "ordering_property_local": "",
                "parent_parameter": "parent_iri",
                "parent_predicate_local": "hasSheet",
                "parent_predicate_iri": parent_predicate_iri,
                "parent_unique_incoming": True,
                "parent_binds_to_session_root": True,
                "parent_via_primitive": False,
                "datatype_inputs": [
                    {
                        "property_local": "sheetName",
                        "property_iri": name_iri,
                        "python_type": "str",
                    }
                ],
                "quantities": [],
                "parent_quantities": [],
                "fresh_dependents": [],
                "reusable_links": [],
                "nested_reusable_links": [],
                "identity_contract": {
                    "kind": "unique_parent",
                    "identity_args": ["parent_iri"],
                },
            }
        ],
        "public_linkers": [],
        "reusable_classes": [],
    }
    graph = Graph()
    skeleton = URIRef("urn:test:skeleton")
    graph.add((URIRef(root_iri), URIRef(parent_predicate_iri), skeleton))
    graph.add((skeleton, RDF.type, URIRef(class_iri)))
    graph.add((skeleton, RDFS.label, Literal("placeholder")))

    def success_json(*, iri: str = "", message: str = "", **metadata: object) -> str:
        return json.dumps(
            {"status": "ok", "iri": iri, "message": message, **metadata},
            sort_keys=True,
        )

    @contextmanager
    def transaction():
        yield graph

    runtime = SimpleNamespace(
        retained_graph=lambda: graph,
        atomic_graph_transaction=transaction,
        success_json=success_json,
        error_json=lambda **metadata: json.dumps(
            {"status": "rejected", **metadata}, sort_keys=True
        ),
        bind_root_argument=lambda requested: {
            "requested_root_iri": requested,
            "effective_root_iri": root_iri,
            "root_argument_canonicalized": requested != root_iri,
            "binding_source": "session",
        },
        register_semantic_rejection=lambda *_args, **_kwargs: None,
        resolve_semantic_skip=lambda *_args, **_kwargs: "{}",
    )
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="example"),
        contract={"occurrence_surface_units": compiled},
    )
    tree = ast.parse(emit_occurrence_operations(context, compiled))
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, ast.ImportFrom) or node.level == 0
    ]
    namespace = {
        "rdf_runtime": runtime,
        "entities": SimpleNamespace(
            create_Sheet=lambda **_kwargs: success_json(iri="urn:test:fresh")
        ),
        "relationships": SimpleNamespace(
            add_hasSheet=lambda *_args: success_json(iri=root_iri)
        ),
    }
    exec(compile(tree, "<unique-parent-reuse>", "exec"), namespace)
    reused = json.loads(
        namespace["create_Sheet"]("later", root_iri, sheetName="Hans")
    )
    assert reused["iri"] == str(skeleton)
    assert reused["already_committed"] is True
    assert reused["graph_changed"] is True
    assert (skeleton, URIRef(name_iri), Literal("Hans")) in graph
    replayed = json.loads(
        namespace["create_Sheet"]("later", root_iri, sheetName="Other")
    )
    assert replayed["iri"] == str(skeleton)
    assert replayed["graph_changed"] is False
    assert (skeleton, URIRef(name_iri), Literal("Other")) not in graph


def _unique_parent_runtime(graph: Graph, *, root_iri: str):
    def success_json(*, iri: str = "", message: str = "", **metadata: object) -> str:
        return json.dumps(
            {"status": "ok", "iri": iri, "message": message, **metadata},
            sort_keys=True,
        )

    @contextmanager
    def transaction():
        yield graph

    return (
        SimpleNamespace(
            retained_graph=lambda: graph,
            atomic_graph_transaction=transaction,
            success_json=success_json,
            error_json=lambda **metadata: json.dumps(
                {"status": "rejected", **metadata}, sort_keys=True
            ),
            bind_root_argument=lambda requested: {
                "requested_root_iri": requested,
                "effective_root_iri": root_iri,
                "root_argument_canonicalized": requested != root_iri,
                "binding_source": "session",
            },
            register_semantic_rejection=lambda *_args, **_kwargs: None,
            resolve_semantic_skip=lambda *_args, **_kwargs: "{}",
        ),
        success_json,
    )


def _unique_parent_output_tool(*, representation_iri: str) -> dict:
    return {
        "name": "create_Output",
        "primitive_tool": "create_Output",
        "owner_class_iri": NS + "Output",
        "ordering_property_local": "",
        "parent_parameter": "parent_iri",
        "parent_predicate_local": "containsOutput",
        "parent_predicate_iri": NS + "containsOutput",
        "parent_unique_incoming": True,
        "parent_binds_to_session_root": True,
        "parent_via_primitive": False,
        "datatype_inputs": [],
        "quantities": [],
        "parent_quantities": [],
        "fresh_dependents": [],
        "reusable_links": [
            {
                "label_parameter": "isRepresentedBy_label",
                "predicate_local": "isRepresentedBy",
                "predicate_iri": NS + "isRepresentedBy",
                "target_class_local": "Representation",
                "target_class_iri": representation_iri,
                "create_tool": "create_Representation",
                "create_fresh_with_datatypes": True,
                "default_label_from_owner": True,
                "datatype_inputs": [
                    {
                        "property_local": "hasCode",
                        "parameter_name": "isRepresentedBy_hasCode",
                    }
                ],
            }
        ],
        "nested_reusable_links": [],
        "identity_contract": {
            "kind": "unique_parent",
            "identity_args": ["parent_iri"],
        },
    }


def test_unique_parent_create_defaults_representation_from_owner_label() -> None:
    root_iri = "urn:test:root"
    output_iri = "urn:test:output"
    representation_iri = NS + "Representation"
    created_repr = "urn:test:representation"
    compiled = {
        "public_tools": [_unique_parent_output_tool(representation_iri=representation_iri)],
        "public_linkers": [],
        "reusable_classes": [],
    }
    graph = Graph()
    runtime, success_json = _unique_parent_runtime(graph, root_iri=root_iri)
    created_labels: list[str] = []
    links: list[tuple[str, str]] = []

    def create_output(**kwargs):
        graph.add((URIRef(output_iri), RDF.type, URIRef(NS + "Output")))
        graph.add((URIRef(output_iri), RDFS.label, Literal(kwargs["label"])))
        return success_json(iri=output_iri)

    def create_representation(*, label, hasCCDCNumber=None, hasCode=None):
        created_labels.append(label)
        graph.add((URIRef(created_repr), RDF.type, URIRef(representation_iri)))
        graph.add((URIRef(created_repr), RDFS.label, Literal(label)))
        return success_json(iri=created_repr)

    def add_contains_output(subject_iri, object_iri):
        graph.add((URIRef(subject_iri), URIRef(NS + "containsOutput"), URIRef(object_iri)))
        return success_json(iri=object_iri)

    def add_is_represented_by(subject_iri, object_iri):
        links.append((subject_iri, object_iri))
        graph.add((URIRef(subject_iri), URIRef(NS + "isRepresentedBy"), URIRef(object_iri)))
        return success_json(iri=object_iri)

    context = SimpleNamespace(
        ontology=SimpleNamespace(name="example"),
        contract={"occurrence_surface_units": compiled},
    )
    tree = ast.parse(emit_occurrence_operations(context, compiled))
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, ast.ImportFrom) or node.level == 0
    ]
    namespace = {
        "rdf_runtime": runtime,
        "entities": SimpleNamespace(
            create_Output=create_output,
            create_Representation=create_representation,
        ),
        "relationships": SimpleNamespace(
            add_containsOutput=add_contains_output,
            add_isRepresentedBy=add_is_represented_by,
        ),
    }
    exec(compile(tree, "<unique-parent-default-repr>", "exec"), namespace)
    created = json.loads(namespace["create_Output"]("product-1", root_iri))
    assert created["iri"] == output_iri
    assert created_labels == ["product-1"]
    assert links == [(output_iri, created_repr)]
    assert (
        URIRef(output_iri),
        URIRef(NS + "isRepresentedBy"),
        URIRef(created_repr),
    ) in graph


def test_unique_parent_reuse_fills_missing_representation_link() -> None:
    root_iri = "urn:test:root"
    output_iri = "urn:test:output"
    representation_iri = NS + "Representation"
    created_repr = "urn:test:representation"
    compiled = {
        "public_tools": [_unique_parent_output_tool(representation_iri=representation_iri)],
        "public_linkers": [],
        "reusable_classes": [],
    }
    graph = Graph()
    graph.add((URIRef(root_iri), URIRef(NS + "containsOutput"), URIRef(output_iri)))
    graph.add((URIRef(output_iri), RDF.type, URIRef(NS + "Output")))
    graph.add((URIRef(output_iri), RDFS.label, Literal("product-1")))
    runtime, success_json = _unique_parent_runtime(graph, root_iri=root_iri)
    created_labels: list[str] = []

    def create_representation(*, label, hasCCDCNumber=None, hasCode=None):
        created_labels.append(label)
        graph.add((URIRef(created_repr), RDF.type, URIRef(representation_iri)))
        graph.add((URIRef(created_repr), RDFS.label, Literal(label)))
        return success_json(iri=created_repr)

    def add_is_represented_by(subject_iri, object_iri):
        graph.add((URIRef(subject_iri), URIRef(NS + "isRepresentedBy"), URIRef(object_iri)))
        return success_json(iri=object_iri)

    context = SimpleNamespace(
        ontology=SimpleNamespace(name="example"),
        contract={"occurrence_surface_units": compiled},
    )
    tree = ast.parse(emit_occurrence_operations(context, compiled))
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, ast.ImportFrom) or node.level == 0
    ]
    namespace = {
        "rdf_runtime": runtime,
        "entities": SimpleNamespace(
            create_Output=lambda **_kwargs: success_json(iri="urn:test:fresh"),
            create_Representation=create_representation,
        ),
        "relationships": SimpleNamespace(
            add_containsOutput=lambda *_args: success_json(iri=root_iri),
            add_isRepresentedBy=add_is_represented_by,
        ),
    }
    exec(compile(tree, "<unique-parent-reuse-repr>", "exec"), namespace)
    reused = json.loads(namespace["create_Output"]("product-1", root_iri))
    assert reused["iri"] == output_iri
    assert reused["already_committed"] is True
    assert reused["graph_changed"] is True
    assert created_labels == ["product-1"]
    assert (
        URIRef(output_iri),
        URIRef(NS + "isRepresentedBy"),
        URIRef(created_repr),
    ) in graph
    replayed = json.loads(namespace["create_Output"]("other-label", root_iri))
    assert replayed["iri"] == output_iri
    assert replayed["graph_changed"] is False
    assert created_labels == ["product-1"]
