from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.domain_generation_config import (
    PLANNING_MODEL,
    WORKFLOW_PROFILES,
    load_domain_generation_config,
)


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_CONFIG = ROOT / "configs" / "domains" / "ontosynthesis.json"


def _planner(model: str, prompt: str) -> dict:
    assert model == "gpt-5"
    if "Select the single top entity class" in prompt:
        return {
            "class_local": "ChemicalSynthesis",
            "rationale": "It organizes the synthesis subgraph.",
            "evidence": ["ChemicalSynthesis", "hasChemicalInput", "hasSynthesisStep"],
        }
    return {
        "iterations": [
            {
                "iteration_number": 2,
                "name": "inputs_outputs",
                "description": "Inputs, outputs, provenance, and document context.",
                "responsibilities": {
                    "classes": [
                        "ChemicalInput",
                        "ChemicalOutput",
                        "Document",
                        "DocumentContext",
                        "MetalOrganicPolyhedron",
                        "Supplier",
                    ],
                    "object_properties": [
                        "hasChemicalInput",
                        "hasChemicalOutput",
                        "retrievedFrom",
                        "hasDocumentContext",
                        "isRepresentedBy",
                        "isSuppliedBy",
                        "referencesMaterial",
                    ],
                },
                "requires_pre_extraction": False,
                "enrichment_focus": [],
            },
            {
                "iteration_number": 3,
                "name": "synthesis_steps",
                "description": "Ordered synthesis operations and their linked context.",
                "responsibilities": {
                    "classes": [
                        "SynthesisStep",
                        "Add",
                        "Stir",
                        "HeatChill",
                        "Evaporate",
                        "Sonicate",
                        "Transfer",
                        "Separate",
                        "Filter",
                        "Dry",
                        "Vessel",
                        "VesselType",
                        "VesselEnvironment",
                        "Equipment",
                        "HeatChillDevice",
                        "SeparationType",
                    ],
                    "object_properties": [
                        "hasSynthesisStep",
                        "hasAddedChemicalInput",
                        "hasStepDuration",
                        "hasVessel",
                        "hasVesselType",
                        "hasVesselEnvironment",
                        "usesEquipment",
                        "hasEquipment",
                        "hasTargetTemperature",
                        "hasTemperatureRate",
                        "hasHeatChillDevice",
                        "hasWashingSolvent",
                        "hasSeparationSolvent",
                        "isSeparationType",
                        "hasTransferedAmount",
                        "isTransferedTo",
                        "hasDryingAgent",
                        "hasDryingPressure",
                        "hasDryingTemperature",
                        "hasEvaporationPressure",
                        "hasEvaporationTemperature",
                        "isEvaporatedToVolume",
                        "removesSpecies",
                        "hasCrystallizationTargetTemperature",
                    ],
                },
                "requires_pre_extraction": True,
                "enrichment_focus": [
                    {
                        "name": "step_enrichment",
                        "description": "Refine ordered step types.",
                    },
                    {
                        "name": "vessel_enrichment",
                        "description": "Enrich vessel and equipment context.",
                    },
                ],
            },
            {
                "iteration_number": 4,
                "name": "yield_extraction",
                "description": "Reported yield.",
                "responsibilities": {
                    "classes": ["Yield"],
                    "object_properties": ["hasYield"],
                },
                "requires_pre_extraction": False,
                "enrichment_focus": [],
            },
        ]
    }


def test_domain_config_is_runtime_only_and_planners_are_gpt5() -> None:
    config = load_domain_generation_config(
        DOMAIN_CONFIG, repository_root=ROOT
    )

    assert config.workflow_profile == "complex"
    assert config.profile == WORKFLOW_PROFILES["complex"]
    assert config.models["top_entity_planning"] == PLANNING_MODEL == "gpt-5"
    assert config.models["iteration_planning"] == "gpt-5"
    raw = json.loads(DOMAIN_CONFIG.read_text(encoding="utf-8"))
    assert "classes" not in json.dumps(raw)
    assert "responsibilities" not in json.dumps(raw)


def test_domain_config_rejects_semantic_overrides(tmp_path: Path) -> None:
    raw = json.loads(DOMAIN_CONFIG.read_text(encoding="utf-8"))
    raw["top_entity"] = "ChemicalSynthesis"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden semantic field"):
        load_domain_generation_config(path, repository_root=ROOT)


def test_compiled_ontosynthesis_plan_preserves_pipeline_compatibility(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )

    assert context.contract["top_entity"]["class_local"] == "ChemicalSynthesis"
    assert context.contract["top_entity"]["model"] == "gpt-5"
    iterations = context.iteration_blueprint["iterations"]
    assert [item["iteration_number"] for item in iterations] == [2, 3, 4]
    assert [item["model_config_key"] for item in iterations] == [
        "iter2_hints",
        "iter3_hints",
        "iter4_hints",
    ]
    assert [item["use_agent"] for item in iterations] == [True, False, False]
    assert iterations[1]["has_pre_extraction"] is True
    assert iterations[1]["pre_extraction_model_key"] == "advanced_model"
    assert [
        item["model_config_key"] for item in iterations[1]["sub_iterations"]
    ] == ["iter3_1_enrichment", "iter3_2_enrichment"]
    assert iterations[0]["extraction_mcp_set_name"] == "chemistry.json"
    assert iterations[0]["extraction_mcp_tools"] == [
        "pubchem",
        "enhanced_websearch",
        "ccdc",
    ]
    assert (
        iterations[2]["inputs"]["file_path"]
        == "extracted_data/{entity_safe}/synthesis_steps_enriched.json"
    )
    assert "DocumentContext" in {
        item["local"] for item in iterations[0]["semantic_scope"]["classes"]
    }
    assert "retrievedFrom" in {
        item["local"]
        for item in iterations[0]["semantic_scope"]["object_properties"]
    }

    manifest = json.loads(
        (
            Path(context.ontology_structure_dir)
            / "domain_artifact_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["planning_model"] == "gpt-5"
    assert manifest["schema_version"] == "domain-artifact-manifest.v1"
    assert context.config_provenance["boundary"]["manual_inputs"] == [
        "tbox_bundle",
        "domain_config",
    ]


def test_profile_shape_failure_is_fail_closed(tmp_path: Path) -> None:
    def bad_planner(model: str, prompt: str) -> dict:
        if "Select the single top entity class" in prompt:
            return _planner(model, prompt)
        return {"iterations": []}

    with pytest.raises(ValueError, match="requires 3 main iterations"):
        build_domain_generation_context(
            domain_config_path=DOMAIN_CONFIG,
            output_root=tmp_path,
            repository_root=ROOT,
            write_files=False,
            planner=bad_planner,
        )
