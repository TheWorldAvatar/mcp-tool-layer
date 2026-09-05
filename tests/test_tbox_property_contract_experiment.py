from __future__ import annotations

from pathlib import Path

from src.agents.scripts_and_prompts_generation.tbox_property_contract_experiment import (
    contract_sha256,
    derive_iteration_property_contract,
    render_property_contract_block,
    reverse_mapping_order,
)
from src.agents.scripts_and_prompts_generation.ttl_parser import (
    extract_ontology_integrity_profile,
    parse_ontology_ttl,
)


ROOT = Path(__file__).resolve().parents[1]


def _synthetic_inputs() -> tuple[dict, dict]:
    parsed = {
        "classes": {
            "Step": {
                "datatype_properties": {
                    "hasOrder": "integer",
                    "isApplicable": "boolean",
                },
                "object_properties": {
                    "usesTarget": "Target",
                    "deferredProperty": "Target",
                },
            },
            "Target": {
                "datatype_properties": {},
                "object_properties": {},
            },
            "Result": {
                "datatype_properties": {},
                "object_properties": {},
            },
        },
        "properties": {
            "hasOrder": {
                "iri": "https://example.test/hasOrder",
                "kind": "datatype",
                "domains": ["Step"],
                "range": "integer",
            },
            "isApplicable": {
                "iri": "https://example.test/isApplicable",
                "kind": "datatype",
                "domains": ["Step"],
                "range": "boolean",
            },
            "usesTarget": {
                "iri": "https://example.test/usesTarget",
                "kind": "object",
                "domains": ["Step"],
                "range": "Target",
            },
            "deferredProperty": {
                "iri": "https://example.test/deferredProperty",
                "kind": "object",
                "domains": ["Step"],
                "range": "Target",
            },
            "hasStep": {
                "iri": "https://example.test/hasStep",
                "kind": "object",
                "domains": ["Root"],
                "range": "Step",
            },
            "hasYield": {
                "iri": "https://example.test/hasYield",
                "kind": "datatype",
                "domains": ["Root"],
                "range": "double",
            },
        },
    }
    plan = {
        "iterations": [
            {
                "iteration_number": 3,
                "responsibilities": {
                    "classes": ["Step", "Target"],
                    "object_properties": ["hasStep"],
                },
            },
            {
                "iteration_number": 4,
                "responsibilities": {
                    "classes": ["Result"],
                    "object_properties": ["hasYield", "deferredProperty"],
                },
            },
        ]
    }
    return parsed, plan


def test_derivation_combines_class_properties_and_explicit_bridges() -> None:
    parsed, plan = _synthetic_inputs()
    contract = derive_iteration_property_contract(
        parsed=parsed,
        compiled_plan=plan,
        iteration_number=3,
    )

    properties = {item["local"]: item for item in contract["properties"]}
    assert set(properties) == {
        "hasOrder",
        "isApplicable",
        "usesTarget",
        "hasStep",
    }
    assert properties["hasOrder"]["kind"] == "datatype"
    assert properties["usesTarget"]["kind"] == "object"
    assert properties["hasStep"]["sources"]["bridge"] is True
    assert contract["excluded_properties"] == [
        {
            "local": "deferredProperty",
            "reason": "explicitly_owned_by_other_iteration",
            "owners": ["4"],
            "class_sources": ["Step"],
        }
    ]
    assert contract["diagnostics"]["unresolved_properties"] == []


def test_derivation_is_invariant_to_mapping_order() -> None:
    parsed, plan = _synthetic_inputs()
    baseline = derive_iteration_property_contract(
        parsed=parsed,
        compiled_plan=plan,
        iteration_number=3,
    )
    reordered = derive_iteration_property_contract(
        parsed=reverse_mapping_order(parsed),
        compiled_plan=reverse_mapping_order(plan),
        iteration_number=3,
    )

    assert contract_sha256(baseline) == contract_sha256(reordered)


def test_generic_generation_contract_has_no_ontosynthesis_local_semantics() -> None:
    source = (
        ROOT
        / "src"
        / "agents"
        / "scripts_and_prompts_generation"
        / "pure_llm_generation.py"
    ).read_text(encoding="utf-8")
    forbidden_literals = {
        "OntoSynthesis",
        "ChemicalSynthesis",
        "Crystallize",
        "hasOrder",
        "hasSynthesisStep",
        "materialize_in_document_procedure_reference_as_complete_workflow",
        "do_not_materialize_procedure_reference_edge",
        "prohibit_instance_creation",
    }

    assert all(literal not in source for literal in forbidden_literals)


def test_ontosynthesis_iteration_three_boundary_is_complete() -> None:
    tbox_path = ROOT / "data" / "ontologies" / "ontosynthesis.ttl"
    tbox = tbox_path.read_text(encoding="utf-8")
    parsed = parse_ontology_ttl(str(tbox_path))
    assert "inheritsFromProcedure" not in parsed["properties"]
    assert all(
        "\r" not in str(spec.get("comment") or "")
        for group in ("classes", "properties")
        for spec in parsed[group].values()
    )
    assert "integrity_annotations" not in parsed["classes"]["Crystallize"]
    assert "Use Crystallize only when" in parsed["classes"]["Crystallize"]["comment"]
    for predicate in (
        "instanceIntegrityRule",
        "edgeIntegrityRule",
        "orderingSemantics",
        "typingIntegrityRule",
    ):
        assert predicate not in tbox
        assert predicate not in (
            ROOT
            / "src"
            / "agents"
            / "scripts_and_prompts_generation"
            / "ttl_parser.py"
        ).read_text(encoding="utf-8")

    profile = extract_ontology_integrity_profile(str(tbox_path))
    assert set(profile) == {
        "class_constraints",
        "property_constraints",
        "ordered_member_classes",
        "non_reusable_classes",
        "parent_type_preserving_classes",
        "most_specific_subclass_targets",
        "individually_linked_object_properties",
        "single_valued_ordering_properties",
    }
    assert "SynthesisStep" in profile["most_specific_subclass_targets"]
    plan = {
        "iterations": [
            {
                "iteration_number": 3,
                "responsibilities": {
                    "classes": [
                        "Add",
                        "Stir",
                        "HeatChill",
                        "Evaporate",
                        "Sonicate",
                        "Transfer",
                        "Separate",
                        "Filter",
                        "Dry",
                        "Crystallize",
                        "Equipment",
                        "HeatChillDevice",
                        "Vessel",
                        "VesselType",
                        "VesselEnvironment",
                        "SeparationType",
                    ],
                    "object_properties": ["hasSynthesisStep"],
                },
            },
            {
                "iteration_number": 4,
                "responsibilities": {
                    "classes": [
                        "MetalOrganicPolyhedron",
                        "Supplier",
                        "ExecutionPoint",
                    ],
                    "object_properties": [
                        "isRepresentedBy",
                        "hasYield",
                        "isSuppliedBy",
                        "hasEquipment",
                        "inheritsFromProcedure",
                    ],
                },
            },
        ]
    }
    contract = derive_iteration_property_contract(
        parsed=parsed,
        compiled_plan=plan,
        iteration_number=3,
    )

    properties = {item["local"]: item for item in contract["properties"]}
    assert {
        "isLayered",
        "isLayeredTransfer",
        "isRepeated",
        "isVacuumFiltration",
        "isWait",
        "hasTargetPh",
        "hasSynthesisStep",
    } <= set(properties)
    assert properties["isRepeated"]["kind"] == "datatype"
    assert properties["isRepeated"]["range"] == "integer"
    assert properties["hasSynthesisStep"]["sources"]["bridge"] is True
    assert "hasYield" not in properties
    assert "inheritsFromProcedure" not in properties
    assert "hasCrystallizationTargetTemperature" in properties
    assert contract["excluded_classes"] == []
    assert contract["excluded_class_rules"] == []
    rendered = render_property_contract_block(contract)
    assert "BEGIN GENERATED TBOX PROPERTY CONTRACT" in rendered
    assert "isRepeated | datatype | domain=Filter | range=integer" in rendered
    assert "Excluded classes and their exclusive properties" not in rendered
    assert contract["diagnostics"] == {
        "unknown_classes": [],
        "unresolved_properties": [],
        "kind_mismatches": [],
        "multiple_explicit_owners": [],
    }
