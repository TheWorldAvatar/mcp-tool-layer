from __future__ import annotations

import pytest

from src.agents.scripts_and_prompts_generation.tool_description_generation import (
    _build_mcp_instruction,
    _creator_companion_recipes,
    _validate_companion_recipes,
    _validate_description_compaction,
)


def test_creator_companion_recipes_name_exact_target_and_relationship_tools() -> None:
    recipes = _creator_companion_recipes(
        names=["create_Owner"],
        all_tool_names={
            "create_Owner",
            "create_Dependent",
            "create_Target",
            "add_hasOwnedDependent",
            "add_hasTarget",
        },
        units=[
            {
                "public_tool": "create_Owner",
                "creator_contract": {
                    "public_tool": "create_Owner",
                    "class_iri": "urn:class:Owner",
                    "required_edges": [
                        {"predicate_local": "hasOwnedDependent"}
                    ],
                },
            }
        ],
        relationship_contracts={
            "hasOwnedDependent": {
                "domain_iris": ["urn:class:Owner"],
                "creator_tools": ["create_Dependent"],
            },
            "hasTarget": {
                "domain_iris": ["urn:class:Owner"],
                "creator_tools": ["create_Target"],
            },
        },
        external_creator_specs=[],
    )

    assert recipes["create_Owner"] == [
        {
            "sequence": "create_Target → add_hasTarget",
            "target_creator_tool": "create_Target",
            "relationship_tool": "add_hasTarget",
            "relationship": "hasTarget",
            "target_class_iris": [],
            "target_creator_arguments": {},
            "subject": "IRI returned by create_Owner (relationship subject)",
            "object": "IRI returned by create_Target (relationship object)",
            "condition": "only when this relation is supported by source evidence",
        }
    ]


def test_companion_recipe_validation_rejects_missing_exact_sequence() -> None:
    contract = {
        "creator_companion_recipes": {
            "create_Owner": [
                {"sequence": "create_Target → add_hasTarget"}
            ]
        }
    }

    with pytest.raises(ValueError, match="missing exact recipe"):
        _validate_companion_recipes(
            descriptions={
                "create_Owner": "Companion calls when evidenced: create a target."
            },
            source_contract=contract,
        )

    _validate_companion_recipes(
        descriptions={
            "create_Owner": (
                "Companion calls when evidenced: "
                "create_Target → add_hasTarget."
            )
        },
        source_contract=contract,
    )


def test_companion_recipe_validation_requires_exact_target_creator_input() -> None:
    class_iri = "urn:class:Target"
    contract = {
        "creator_companion_recipes": {
            "create_Owner": [
                {
                    "sequence": "create_quantity → add_hasTarget",
                    "target_class_iris": [class_iri],
                    "target_creator_arguments": {
                        "quantity_class_iri": class_iri
                    },
                }
            ]
        }
    }

    with pytest.raises(ValueError, match="missing target class IRI"):
        _validate_companion_recipes(
            descriptions={
                "create_Owner": (
                    "Companion calls when evidenced: "
                    "create_quantity → add_hasTarget."
                )
            },
            source_contract=contract,
        )

    _validate_companion_recipes(
        descriptions={
            "create_Owner": (
                "Companion calls when evidenced: create_quantity → add_hasTarget; "
                f"set quantity_class_iri={class_iri}."
            )
        },
        source_contract=contract,
    )


def test_description_compaction_rejects_reexpanded_generic_prose() -> None:
    with pytest.raises(ValueError, match="compaction validation failed"):
        _validate_description_compaction(
            descriptions={"add_hasTarget": "x" * 1001},
            source_contract={},
        )

    _validate_description_compaction(
        descriptions={
            "add_hasTarget": "Compact endpoint-specific relation.",
            "create_Owner": "Companion calls when evidenced: None.",
        },
        source_contract={"creator_companion_recipes": {"create_Owner": []}},
    )


def test_mcp_instruction_reconciles_views_and_preserves_distinct_occurrences() -> None:
    instruction = _build_mcp_instruction(
        {
            "reuse_policy": {
                "classes": [
                    {
                        "class_iri": "urn:class:Target",
                        "reusable": False,
                    }
                ]
            },
            "relationship_tool_contracts": {
                "hasTarget": {
                    "range_iris": ["urn:class:Target"],
                    "creator_tools": ["create_Target"],
                    "target_handling": "generated_creator",
                },
                "hasQuantity": {
                    "range_iris": ["urn:class:Quantity"],
                    "creator_tools": ["create_om2_quantity"],
                    "target_handling": "fixed_runtime_creator",
                },
            },
            "materialization_operation_units": {
                "units": [
                    {
                        "public_tool": "create_Owner",
                        "creator_contract": {
                            "public_tool": "create_Owner",
                            "ordered_member": True,
                            "ordering_property_local": "hasPosition",
                            "required_edges": [
                                {
                                    "role": "container_membership",
                                    "predicate_local": "hasMember",
                                    "container_class_iris": [
                                        "urn:class:Container"
                                    ],
                                },
                                {
                                    "role": "owned_dependent",
                                    "lifecycle": "fresh_per_owner",
                                    "dependent_class_local": "Dependent",
                                    "predicate_local": "hasOwnedDependent",
                                    "cardinality": "exactly_one",
                                    "exclusive_predicate_iris": [
                                        "urn:property:hasOwnedDependent"
                                    ],
                                }
                            ],
                        },
                    },
                    {
                        "public_tool": "create_Other",
                        "creator_contract": {
                            "public_tool": "create_Other",
                            "ordered_member": True,
                            "ordering_property_local": "hasPosition",
                            "required_edges": [
                                {
                                    "role": "container_membership",
                                    "predicate_local": "hasMember",
                                    "container_class_iris": [
                                        "urn:class:Container"
                                    ],
                                }
                            ],
                        },
                    },
                ]
            }
        }
    )

    assert "Never simulate tool calls" in instruction
    assert "a plan or serialized call description is not an executed graph mutation" in instruction
    assert "emit every independent mutation whose inputs are already known" in instruction
    assert "Do not first write an occurrence ledger" in instruction
    assert "keep only the first presentation and ignore later copies" in instruction
    assert "must not create another individual or another 1..N sequence" in instruction
    assert "Distinct source spans, headings" in instruction
    assert "one canonical 1..N sequence taken from the first presentation" in instruction
    assert (
        "The creator tools `create_Other`, `create_Owner` participate in one shared "
        "ordered collection"
    ) in instruction
    assert "through `hasMember` and use the same `hasPosition` position" in instruction
    assert "creator type must never start a separate counter" in instruction
    assert "translate them into the shared global 1..N sequence" in instruction
    assert "never submit duplicate positions" in instruction
    assert "owner IRI denotes the owner class only" in instruction
    assert "must satisfy that relationship's domain and range" in instruction
    assert "form one occurrence ledger" not in instruction
    assert "Classify every ledger obligation" not in instruction
    assert "Execute the ledger in dependency order" not in instruction
    assert "Before every relationship call, verify" not in instruction
    assert "`add_hasTarget` has a non-reusable occurrence-local object slot" in instruction
    assert "`urn:class:Target`" in instruction
    assert "`add_hasQuantity` has a non-reusable occurrence-local object slot" in instruction
    assert 'quantity_class_iri="urn:class:Quantity"' in instruction
    assert "`create_Owner` is a contract-declared ordered-occurrence creator" in instruction
    assert "using `hasPosition` for the contiguous position" in instruction
    assert "For every distinct `create_Owner` owner occurrence" in instruction
    assert "fresh occurrence-local `Dependent`" in instruction
    assert "never reuse that dependent" in instruction
    assert "every genuinely distinct owner occurrence requires its own dependent" in instruction
    assert "ITER" not in instruction
