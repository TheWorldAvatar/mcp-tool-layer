from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from rdflib import URIRef

from src.agents.scripts_and_prompts_generation import fixed_rdf_runtime
from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    _iteration_kg_prompt,
    generate_deterministic_script_slice,
)
from src.agents.scripts_and_prompts_generation.artifact_surface_contract import (
    _literal_all_manifest,
)
from src.agents.scripts_and_prompts_generation.legacy_script_generation import (
    generate_legacy_script_slice,
)
from src.agents.scripts_and_prompts_generation.materialization_operation_inference import (
    _judge_prompt,
    infer_materialization_operation_decisions,
)
from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
    compile_materialization_operation_units,
    discover_materialization_operation_candidates,
    operation_creator_contracts,
    standalone_relationship_tool_contracts,
)
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _artifact_role_contract,
    _prompt_artifact_generation_contract,
)


NS = "https://example.test/ontology/"
XSD = "http://www.w3.org/2001/XMLSchema#"


def _fixture_contract(*, enabled: bool) -> tuple[dict, dict]:
    parsed = {
        "classes": {
            "Container": {"iri": NS + "Container", "parent_classes": []},
            "Member": {"iri": NS + "Member", "parent_classes": []},
            "Dependent": {"iri": NS + "Dependent", "parent_classes": []},
        },
        "properties": {
            "orderValue": {"comment": ""},
            "dependentText": {"comment": ""},
            "containsMember": {
                "comment": "Every ordered member must link to one parent container."
            },
            "ownsDependent": {
                "comment": (
                    "Each owner has exactly one fresh owner-local dependent occurrence."
                )
            },
        },
    }
    contract = {
        "ontology_publish_contract": {
            "subclass_closure": [
                {
                    "class_iri": NS + "Member",
                    "superclass_iris": [],
                }
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
        },
        "reuse_policy": {
            "classes": [
                {
                    "class_iri": NS + "Dependent",
                    "class_local": "Dependent",
                    "reusable": False,
                    "reuse_scope": "occurrence_local",
                }
            ]
        },
    }
    if enabled:
        candidates = discover_materialization_operation_candidates(
            parsed=parsed,
            contract=contract,
        )
        contract["materialization_operation_candidates"] = candidates
        contract["materialization_operation_decisions"] = {
            "schema_version": "materialization-operation-decisions.v1",
            "decisions": [
                {
                    "candidate_id": candidate["candidate_id"],
                    "decision": "merge",
                }
                for candidate in candidates["candidates"]
            ],
        }
    return parsed, contract


def test_compiles_required_edges_into_one_owner_operation() -> None:
    parsed, contract = _fixture_contract(enabled=True)
    compiled = compile_materialization_operation_units(
        parsed=parsed,
        contract=contract,
    )

    assert compiled["errors"] == []
    assert compiled["merged_predicate_locals"] == [
        "containsMember",
        "ownsDependent",
    ]
    creators = {
        item["class_local"]: item
        for item in operation_creator_contracts(compiled)
    }
    member = creators["Member"]
    assert [edge["role"] for edge in member["required_edges"]] == [
        "container_membership",
        "owned_dependent",
    ]
    assert member["required_edges"][0]["parameter_name"] == "parent_iri"
    assert (
        member["required_edges"][1]["label_parameter"]
        == "owned_ownsDependent_label"
    )
    assert (
        member["required_edges"][1]["datatype_inputs"][0]["parameter_name"]
        == "owned_ownsDependent_dependentText"
    )

    standalone = standalone_relationship_tool_contracts(
        contract["relationship_tool_contracts"],
        compiled,
    )
    assert standalone == {}


def test_default_policy_preserves_legacy_split_surface() -> None:
    parsed, contract = _fixture_contract(enabled=False)
    compiled = compile_materialization_operation_units(
        parsed=parsed,
        contract=contract,
    )

    assert compiled["errors"] == []
    assert compiled["merged_predicate_locals"] == []
    assert all(
        not creator["required_edges"]
        for creator in operation_creator_contracts(compiled)
    )
    assert set(
        standalone_relationship_tool_contracts(
            contract["relationship_tool_contracts"],
            compiled,
        )
    ) == {"containsMember", "ownsDependent"}


def test_llm_prompt_generator_forbids_standalone_merged_writers() -> None:
    role = _artifact_role_contract(
        Path("KG_BUILDING_ITER_3.md"),
        {
            "iteration_spec": {"hint_representation": "semantic-text.v1"},
            "merged_predicate_locals": ["ownsDependent"],
        },
    )

    must = "\n".join(role["must"])
    assert "merged_predicate_locals as an exclusion list" in must
    assert "never name, recommend, or invent an add_<merged-predicate> call" in must


def test_domain_generation_projects_units_into_scripts(tmp_path) -> None:
    context = build_agentic_generation_context(
        ontology_name="ontosynthesis",
        output_root=tmp_path,
        write_files=True,
    )
    candidates = discover_materialization_operation_candidates(
        parsed=context.parsed,
        contract=context.contract,
        iteration_plan=context.iteration_blueprint,
    )
    context.contract["materialization_operation_candidates"] = candidates
    context.contract["materialization_operation_decisions"] = {
        "schema_version": "materialization-operation-decisions.v1",
        "decisions": [
            {
                "candidate_id": candidate["candidate_id"],
                "decision": "merge",
            }
            for candidate in candidates["candidates"]
            if candidate["predicate_local"]
            in {"hasSynthesisStep", "hasAddedChemicalInput"}
        ],
    }
    context.contract["materialization_operation_units"] = (
        compile_materialization_operation_units(
            parsed=context.parsed,
            contract=context.contract,
            iteration_plan=context.iteration_blueprint,
        )
    )
    generate_deterministic_script_slice(context)

    entity_path = next(
        tmp_path.glob("scripts/ontosynthesis/*_creation_entities.py")
    )
    tree = ast.parse(entity_path.read_text(encoding="utf-8"))
    owner_creator = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_Add"
    )
    parameters = {
        argument.arg
        for argument in [
            *owner_creator.args.posonlyargs,
            *owner_creator.args.args,
            *owner_creator.args.kwonlyargs,
        ]
    }
    assert {
        "parent_iri",
        "owned_hasAddedChemicalInput_label",
        "owned_hasAddedChemicalInput_hasAmount",
    } <= parameters

    relationship_path = next(
        tmp_path.glob("scripts/ontosynthesis/*_creation_relationships.py")
    )
    manifest = set(_literal_all_manifest(relationship_path))
    assert "add_hasSynthesisStep" not in manifest
    assert "add_hasAddedChemicalInput" not in manifest

    main_path = tmp_path / "scripts" / "ontosynthesis" / "main.py"
    main_text = main_path.read_text(encoding="utf-8")
    assert "validation_json = _check_ordered_members()" in main_text
    assert "return rdf_runtime.export_memory(doi, top_level_entity_name)" in main_text

    step_iteration = next(
        item
        for item in context.iteration_blueprint["iterations"]
        if str(item.get("iteration_number")) == "3"
    )
    prompt = _iteration_kg_prompt(context, step_iteration)
    create_add_line = next(
        line for line in prompt.splitlines() if line.startswith("- `create_Add` accepts only:")
    )
    assert "`parent_iri`" in create_add_line
    assert "`owned_hasAddedChemicalInput_label`" in create_add_line
    assert "- Relation `hasSynthesisStep`:" not in prompt
    assert "- Relation `hasAddedChemicalInput`:" not in prompt
    assert "- `hasSynthesisStep` ->" not in prompt
    assert "- `hasAddedChemicalInput` ->" not in prompt
    assert "Creator-Owned Atomic Edge Contract:" in prompt
    assert (
        "`create_Add` owns `hasSynthesisStep`: pass the existing scoped parent IRI "
        "as `parent_iri`"
    ) in prompt
    assert (
        "`create_Add` owns `hasAddedChemicalInput` and its fresh `ChemicalInput` target"
    ) in prompt
    assert "then link it individually to the scoped top entity" not in prompt

    llm_prompt_contract = _prompt_artifact_generation_contract(
        context,
        tmp_path / "prompts" / "ontosynthesis" / "KG_BUILDING_ITER_3.md",
    )
    assert "hasSynthesisStep" not in llm_prompt_contract[
        "relationship_target_contracts"
    ]
    assert "hasAddedChemicalInput" not in llm_prompt_contract[
        "relationship_target_contracts"
    ]
    assert "add_hasSynthesisStep" not in {
        item["name"]
        for item in llm_prompt_contract["agent_tool_contract"]["relationship_tools"]
    }
    assert "add_hasAddedChemicalInput" not in {
        item["name"]
        for item in llm_prompt_contract["agent_tool_contract"]["relationship_tools"]
    }
    fixed_signatures = {
        item["name"]: item["exact_call_signature"]
        for item in llm_prompt_contract["agent_tool_contract"]["fixed_creator_tools"]
    }
    assert fixed_signatures["create_om2_quantity"].startswith(
        "create_om2_quantity("
    )


def test_legacy_script_generation_copy_keeps_split_surface(tmp_path) -> None:
    context = build_agentic_generation_context(
        ontology_name="ontosynthesis",
        output_root=tmp_path,
        write_files=True,
    )
    generate_legacy_script_slice(context)

    entity_path = next(
        tmp_path.glob("scripts/ontosynthesis/*_creation_entities.py")
    )
    tree = ast.parse(entity_path.read_text(encoding="utf-8"))
    owner_creator = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "create_Add"
    )
    parameters = {
        argument.arg
        for argument in [
            *owner_creator.args.posonlyargs,
            *owner_creator.args.args,
            *owner_creator.args.kwonlyargs,
        ]
    }
    assert "parent_iri" not in parameters
    assert not {name for name in parameters if name.startswith("owned_")}

    relationship_path = next(
        tmp_path.glob("scripts/ontosynthesis/*_creation_relationships.py")
    )
    manifest = set(_literal_all_manifest(relationship_path))
    assert "add_hasSynthesisStep" in manifest
    assert "add_hasAddedChemicalInput" in manifest


def test_fixed_runtime_rolls_back_composite_mutation() -> None:
    graph = fixed_rdf_runtime.retained_graph()
    snapshot = set(graph)
    triple = (
        URIRef("https://example.test/subject"),
        URIRef("https://example.test/predicate"),
        URIRef("https://example.test/object"),
    )
    try:
        with pytest.raises(RuntimeError):
            with fixed_rdf_runtime.atomic_graph_transaction():
                graph.add(triple)
                raise RuntimeError("rollback")
        assert set(graph) == snapshot
    finally:
        fixed_rdf_runtime.reset_graph(graph)
        for existing in snapshot:
            graph.add(existing)


def test_llm_judgement_consumes_only_code_selected_candidates(tmp_path) -> None:
    context = build_agentic_generation_context(
        ontology_name="ontosynthesis",
        output_root=tmp_path,
        write_files=False,
    )

    def judge(_model: str, prompt: str) -> dict:
        candidates = json.loads(prompt.split("\nCANDIDATES:\n", 1)[1])
        decisions = []
        for candidate in candidates["candidates"]:
            evidence = candidate["tbox_evidence"]
            evidence_text = next(
                text for text in evidence.values() if str(text).strip()
            )
            merge = candidate["kind"] == "container_membership" or (
                "exactly one fresh" in evidence.get("predicate_comment", "").casefold()
            )
            decisions.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "decision": "merge" if merge else "separate",
                    "cardinality": "exactly_one" if merge else "unclear",
                    "lifecycle": (
                        "fresh_per_owner"
                        if candidate["kind"] == "owned_dependent" and merge
                        else "existing_reference"
                    ),
                    "evidence_quotes": [evidence_text[:40]],
                    "rationale": "Judged only from supplied evidence.",
                }
            )
        return {
            "schema_version": "materialization-operation-decisions.v1",
            "decisions": decisions,
        }

    result = infer_materialization_operation_decisions(
        context,
        planner=judge,
        model="test-model",
        checkpoint_path=tmp_path / "decision.json",
    )

    assert result.get("fallback") is None
    candidate_ids = {
        item["candidate_id"]
        for item in context.contract["materialization_operation_candidates"][
            "candidates"
        ]
    }
    assert {item["candidate_id"] for item in result["decisions"]} == candidate_ids
    assert (
        context.contract["materialization_operation_units"]["inference_mode"]
        == "accepted_atomic"
    )


def test_operation_judge_prompt_stabilizes_generic_membership_and_repair() -> None:
    candidates = {
        "schema_version": "materialization-operation-candidates.v1",
        "candidates": [
            {
                "candidate_id": "candidate:member",
                "kind": "container_membership",
                "structural_evidence": {
                    "ordered_member": True,
                    "unique_compatible_membership_predicate": True,
                    "single_valued_ordering_property": True,
                },
                "tbox_evidence": {
                    "predicate_comment": "Each ordered member belongs to its container."
                },
            }
        ],
    }
    previous = {
        "schema_version": "materialization-operation-decisions.v1",
        "decisions": [{"candidate_id": "candidate:member", "decision": "merge"}],
    }
    prompt = _judge_prompt(
        candidates,
        repair_errors=["candidate:member: evidence quote is not verbatim T-Box text"],
        previous_response=previous,
    )

    assert (
        "A container may have many ordered members while each created member still "
        "requires exactly one container."
    ) in prompt
    assert "Do not downgrade such a candidate" in prompt
    assert "copy at least one exact, contiguous substring" in prompt
    assert "Change only the fields identified by these errors." in prompt
    assert '"candidate_id": "candidate:member"' in prompt
    assert "OntoSyn" not in prompt
    assert "ChemicalSynthesis" not in prompt
