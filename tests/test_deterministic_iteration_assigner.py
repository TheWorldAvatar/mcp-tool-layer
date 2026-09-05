from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from scripts.run_iteration_ownership_stability import (
    publish_stable_iteration_artifacts,
    run_stability_experiment,
)
from src.agents.scripts_and_prompts_generation.deterministic_iteration_assigner import (
    assign_iteration_ownership,
)


PROFILE = {
    "slots": [
        {
            "id": "iter2",
            "slot_kind": "foundation",
            "iteration_number": 2,
            "semantic_enrichment_slots": [],
        },
        {
            "id": "iter3",
            "slot_kind": "ordered",
            "iteration_number": 3,
            "semantic_enrichment_slots": [],
        },
        {
            "id": "iter4",
            "slot_kind": "remainder",
            "iteration_number": 4,
            "semantic_enrichment_slots": [],
        },
    ]
}


def _class(local: str, **extra: object) -> dict:
    return {"iri": f"https://example.test/main/{local}", **extra}


def _parsed() -> dict:
    return {
        "classes": {
            "Root": _class("Root"),
            "Input": _class("Input"),
            "ActionBase": _class("ActionBase"),
            "ActionA": _class(
                "ActionA",
                parent_classes=["ActionBase"],
            ),
            "Asset": _class("Asset"),
            "Summary": _class("Summary"),
            "Prohibited": _class(
                "Prohibited",
                creatable=False,
            ),
            "ExternalQuantity": {
                "iri": "https://example.test/external/ExternalQuantity"
            },
        },
        "properties": {
            "hasInput": {
                "iri": "https://example.test/main/hasInput",
                "kind": "object",
                "domains": ["Root"],
                "range": "Input",
            },
            "hasAction": {
                "iri": "https://example.test/main/hasAction",
                "kind": "object",
                "domains": ["Root"],
                "range": "ActionBase",
            },
            "usesInput": {
                "iri": "https://example.test/main/usesInput",
                "kind": "object",
                "domains": ["ActionBase"],
                "range": "Input",
            },
            "usesAsset": {
                "iri": "https://example.test/main/usesAsset",
                "kind": "object",
                "domains": ["ActionBase"],
                "range": "Asset",
            },
            "hasSummary": {
                "iri": "https://example.test/main/hasSummary",
                "kind": "object",
                "domains": ["Root"],
                "range": "Summary",
            },
            "hasMetric": {
                "iri": "https://example.test/main/hasMetric",
                "kind": "object",
                "domains": ["Root"],
                "range": "ExternalQuantity",
            },
            "hasName": {
                "iri": "https://example.test/main/hasName",
                "kind": "datatype",
                "domains": ["Input"],
                "range": "string",
            },
        },
    }


def _contract() -> dict:
    return {
        "external_class_creators": [],
        "required_links": [
            {
                "subject_class_iri": "https://example.test/main/Root",
                "predicate_iri": "https://example.test/main/hasInput",
            }
        ],
        "ordered_member_profile": {
            "ordered_member_classes": ["ActionBase", "ActionA"]
        },
        "reuse_policy": {
            "classes": [
                {"class_local": "Input", "reusable": False},
                {"class_local": "Asset", "reusable": True},
            ]
        },
    }


def _assignment_map(result: dict) -> tuple[dict[str, str], dict[str, str]]:
    ownership = result["ownership_provenance"]
    return ownership["classes"], ownership["object_properties"]


def test_generic_rules_assign_foundation_ordered_and_remainder() -> None:
    result = assign_iteration_ownership(
        profile=PROFILE,
        parsed=_parsed(),
        contract=_contract(),
        top_local="Root",
    )
    classes, properties = _assignment_map(result)

    assert classes == {
        "ActionA": "iter3",
        "Asset": "iter3",
        "Input": "iter2",
        "Summary": "iter2",
    }
    assert "ActionBase" not in classes
    assert "Prohibited" not in classes
    assert "ExternalQuantity" not in classes
    assert properties == {
        "hasAction": "iter3",
        "hasInput": "iter2",
        "hasMetric": "iter4",
        "hasSummary": "iter2",
        "usesAsset": "iter3",
        "usesInput": "iter3",
    }
    assert "hasName" not in properties
    assert "enrichment_focus" not in result


def test_parent_without_creator_is_not_iteration_owned() -> None:
    parsed = {
        "classes": {
            "Root": _class("Root"),
            "Alpha": _class("Alpha"),
            "Beta": _class("Beta", parent_classes=["Alpha"]),
        },
        "properties": {
            "hasAlpha": {
                "iri": "https://example.test/main/hasAlpha",
                "kind": "object",
                "domains": ["Root"],
                "range": "Alpha",
            }
        },
    }
    contract = {
        "external_class_creators": [],
        "required_links": [
            {
                "subject_class_iri": "https://example.test/main/Root",
                "predicate_iri": "https://example.test/main/hasAlpha",
            }
        ],
        "ordered_member_profile": {
            "ordered_member_classes": [],
            "most_specific_subclass_targets": {"Alpha": ["Beta"]},
        },
        "reuse_policy": {"classes": []},
    }
    result = assign_iteration_ownership(
        profile={
            "slots": [
                {
                    "id": "iter2",
                    "slot_kind": "foundation",
                    "iteration_number": 2,
                    "semantic_enrichment_slots": [],
                }
            ]
        },
        parsed=parsed,
        contract=contract,
        top_local="Root",
    )
    classes, properties = _assignment_map(result)
    assert "Alpha" not in classes
    assert "Beta" in classes
    assert "hasAlpha" in properties


def test_assignment_is_invariant_to_mapping_order() -> None:
    parsed = _parsed()
    reversed_parsed = deepcopy(parsed)
    reversed_parsed["classes"] = dict(
        reversed(list(reversed_parsed["classes"].items()))
    )
    reversed_parsed["properties"] = dict(
        reversed(list(reversed_parsed["properties"].items()))
    )
    first = assign_iteration_ownership(
        profile=PROFILE,
        parsed=parsed,
        contract=_contract(),
        top_local="Root",
    )
    second = assign_iteration_ownership(
        profile=PROFILE,
        parsed=reversed_parsed,
        contract=_contract(),
        top_local="Root",
    )

    assert first["ownership_sha256"] == second["ownership_sha256"]
    assert first["assignments"] == second["assignments"]


def test_five_isolated_parallel_runs_are_byte_stable(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]

    def planner_factory(_: int):
        def planner(model: str, prompt: str) -> dict:
            assert model == "gpt-5"
            assert "Select the single top entity class" in prompt
            return {
                "class_local": "ChemicalSynthesis",
                "rationale": "The root organizes the complete synthesis workflow.",
                "evidence": ["ChemicalSynthesis", "hasSynthesisStep"],
            }

        return planner

    report = run_stability_experiment(
        domain_config_path=root / "configs" / "domains" / "ontosynthesis.json",
        output_root=tmp_path,
        repository_root=root,
        runs=5,
        workers=5,
        planner_factory=planner_factory,
    )

    assert report["stable"] is True
    assert all(report["stable_fields"].values())
    assert len(report["records"]) == 5
    published = publish_stable_iteration_artifacts(
        report=report,
        publish_root=tmp_path / "published",
        ontology_name="ontosynthesis",
    )
    assert {Path(path).name for path in published} == {
        "iteration_blueprint.json",
        "iterations.json",
    }

