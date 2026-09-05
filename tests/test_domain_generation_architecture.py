from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    _legacy_adapter,
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.domain_generation_config import (
    PLANNING_MODEL,
    WORKFLOW_PROFILES,
    load_domain_generation_config,
)
from src.agents.scripts_and_prompts_generation.domain_semantic_planner import (
    plan_top_entity_semantics,
)
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _artifact_generation_guidance,
    _artifact_generation_contract,
    _artifact_role_contract,
    _semantic_text_structured_ledger_expectation_failures,
    _existing_entity_check_manifest,
    _required_explicit_ancestor_types,
    _owned_entity_tool_contracts,
    _pre_extraction_candidate_type_contract,
    _prompt_artifact_generation_contract,
    _prompt_tbox_slice,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    _iteration_plan,
    _pre_extraction_prompt,
    generate_deterministic_prompt_slice,
    generate_deterministic_script_slice,
    generate_runtime_support_slice,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    _stage_artifact_contract_report,
    validate_prompt_runtime_bindings,
)
from src.agents.scripts_and_prompts_generation import (
    agentic_generation_runner as generation_runner,
)
from src.agents.scripts_and_prompts_generation import pure_llm_generation
from src.agents.scripts_and_prompts_generation.ttl_parser import parse_ontology_ttl
from src.utils.extraction_models import get_extraction_model


def test_ontosynthesis_runtime_adapter_includes_configured_extensions() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    config = load_domain_generation_config(
        repository_root / "configs" / "domains" / "ontosynthesis.json",
        repository_root=repository_root,
    )
    adapter = _legacy_adapter(
        config,
        blueprint_path=Path("iteration_blueprint.json"),
        top_entity={
            "class_local": "ChemicalSynthesis",
            "class_iri": "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis",
        },
    )

    assert adapter["ontologies"]["main"]["runtime_policies"][
        "ordered_member_contracts"
    ] == [
        {
            "collection_property_iri": (
                "https://www.theworldavatar.com/kg/OntoSyn/hasSynthesisStep"
            ),
            "member_class_iri": (
                "https://www.theworldavatar.com/kg/OntoSyn/SynthesisStep"
            ),
            "order_property_iri": (
                "https://www.theworldavatar.com/kg/OntoSyn/hasOrder"
            ),
        }
    ]
    assert [
        item["name"] for item in adapter["ontologies"]["extensions"]
    ] == ["ontomops", "ontospecies"]
    assert all(
        not Path(item["ttl_file"]).is_absolute()
        for item in adapter["ontologies"]["extensions"]
    )


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_CONFIG = ROOT / "configs" / "domains" / "ontosynthesis.json"


def _namespace(iri: str) -> str:
    if "#" in iri:
        return iri.rsplit("#", 1)[0] + "#"
    return iri.rsplit("/", 1)[0] + "/"


def _expected_primary_classes(context: Any, case: dict) -> set[str]:
    contract = context.contract
    focus = contract.get("extension_focus") or contract.get("top_entity") or {}
    primary_namespace = _namespace(str(focus.get("class_iri") or ""))
    external = {
        item["class_local"]
        for item in contract.get("external_class_creators") or []
    }
    return {
        local
        for local in case["classes"]
        if local not in external
        and _namespace(
            str((context.parsed.get("classes", {}).get(local) or {}).get("iri") or "")
        )
        == primary_namespace
    }


SIMPLE_DOMAIN_CASES = {
    "ontomops": {
        "focus": "MetalOrganicPolyhedron",
        "evidence": ["MetalOrganicPolyhedron", "hasChemicalBuildingUnit"],
        "classes": ["MetalOrganicPolyhedron", "ChemicalBuildingUnit"],
        "properties": ["hasChemicalBuildingUnit", "sameAs"],
        "role": "extension",
        "tools": ["mops_extension", "ccdc"],
    },
    "ontospecies": {
        "focus": "Species",
        "evidence": ["Species", "hasCharacterizationSession"],
        "classes": [
            "Species",
            "AtomicWeight",
            "CCDCNumber",
            "CharacterizationSession",
            "ChemicalFormula",
            "ChemicalShift",
            "Device",
            "Element",
            "ElementalAnalysisData",
            "ElementalAnalysisDevice",
            "HNMRData",
            "HNMRDevice",
            "InfraredBand",
            "InfraredSpectroscopyData",
            "InfraredSpectroscopyDevice",
            "Material",
            "MolecularFormula",
            "Solvent",
            "WeightPercentage",
        ],
        "properties": [
            "hasAtomicWeight",
            "hasCCDCNumber",
            "hasCharacterizationSession",
            "hasChemicalFormula",
            "hasChemicalShift",
            "hasElement",
            "hasElementalAnalysisData",
            "hasElementalAnalysisDevice",
            "hasHNMRData",
            "hasHNMRDevice",
            "hasInfraredBand",
            "hasInfraredSpectroscopyData",
            "hasInfraredSpectroscopyDevice",
            "hasMolecularFormula",
            "hasWeightPercentageCalculated",
            "hasWeightPercentageExperimental",
            "usesDevice",
            "usesMaterial",
            "usesSolvent",
        ],
        "role": "extension",
        "tools": ["ontospecies_extension", "ccdc"],
    },
    "medical": {
        "focus": "MedicalCase",
        "evidence": ["MedicalCase", "hasPatientInfo", "hasProcedure"],
        "classes": [
            "CaseTimeline",
            "Complication",
            "Diagnosis",
            "PathologyOutcome",
            "PatientInfo",
            "Procedure",
            "SurgicalApproach",
            "SurgicalTeam",
        ],
        "properties": [
            "hasComplication",
            "hasDiagnosis",
            "hasPathologyOutcome",
            "hasPatientInfo",
            "hasProcedure",
            "hasSurgicalApproach",
            "hasSurgicalTeam",
            "hasTimeline",
        ],
        "role": "main",
        "tools": ["medical_mcp"],
    },
}


def test_llm_entity_generation_contract_carries_tbox_comment_defaults() -> None:
    class_iri = "https://example.test/Widget"
    property_iri = "https://example.test/enabled"
    context = SimpleNamespace(
        parsed={
            "classes": {
                "Widget": {
                    "iri": class_iri,
                    "parent_classes": [],
                }
            },
            "properties": {
                "enabled": {
                    "comment": "The default value is true unless explicitly overridden."
                }
            },
        },
        contract={
            "ordered_member_profile": {},
            "ontology_publish_contract": {
                "datatype_properties": [
                    {
                        "property_iri": property_iri,
                        "domain_iris": [class_iri],
                        "range_iris": [
                            "http://www.w3.org/2001/XMLSchema#boolean"
                        ],
                    }
                ],
                "subclass_closure": [],
            },
            "external_class_creators": [],
        },
    )

    contracts = _owned_entity_tool_contracts(context)
    datatype_input = contracts[0]["datatype_inputs"][0]
    assert datatype_input["tbox_comment"] == (
        "The default value is true unless explicitly overridden."
    )

    role = _artifact_role_contract(Path("example_creation_entities.py"))
    assert any(
        "binding creator-code responsibility" in rule for rule in role["must"]
    )
    assert any(
        "Never delegate a T-Box-declared base default" in rule
        for rule in role["must"]
    )
    guidance = _artifact_generation_guidance(
        Path("example_creation_entities.py")
    )
    assert "correctly typed T-Box default" in guidance
    assert "owned by the generated creator code" in guidance
    assert "never use contextual rules to justify `None`" in guidance


def test_relationship_meta_contract_is_domain_agnostic_and_policy_bound() -> None:
    target = Path("example_creation_relationships.py")
    role = _artifact_role_contract(target)
    guidance = _artifact_generation_guidance(target)
    meta_text = json.dumps(role, sort_keys=True) + guidance

    assert "class reuse-policy decisions" in meta_text
    assert "non-reusable occurrence reuse rejection" in meta_text
    for domain_marker in (
        "ChemicalInput",
        "OntoSyn",
        "hasAddedChemicalInput",
        "hasWashingSolvent",
    ):
        assert domain_marker not in meta_text


def _planner(model: str, prompt: str) -> dict:
    assert model == "gpt-5"
    if "Select the single top entity class" in prompt:
        return {
            "class_local": "ChemicalSynthesis",
            "rationale": "It organizes the synthesis subgraph.",
            "evidence": ["ChemicalSynthesis", "hasChemicalInput", "hasSynthesisStep"],
        }
    return {
        "assignments": [
            {
                "slot": "iter2",
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
                "rationale": "Inputs, outputs, provenance, and document context.",
            },
            {
                "slot": "iter3",
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
                "rationale": "Ordered synthesis operations and their linked context.",
            },
            {
                "slot": "iter4",
                "classes": ["AmountOfSubstanceFraction"],
                "object_properties": ["hasYield"],
                "rationale": "Reported yield.",
            },
        ],
    }


def _simple_planner(case: dict) -> Callable[[str, str], dict]:
    def planner(model: str, prompt: str) -> dict:
        assert model == "gpt-5"
        if "Select the single top entity class" in prompt:
            if "inherited upstream scoped root" in prompt:
                return {
                    "class_local": "ChemicalSynthesis",
                    "rationale": "It scopes extension passes to an existing synthesis.",
                    "evidence": [
                        "ChemicalSynthesis",
                        "hasChemicalOutput",
                    ],
                }
            return {
                "class_local": case["focus"],
                "rationale": "It organizes the simple ontology graph.",
                "evidence": case["evidence"],
            }
        return {
            "assignments": [
                {
                    "slot": "iter2",
                    "classes": case["classes"],
                    "object_properties": case["properties"],
                    "rationale": "Materialize the remaining simple ontology graph.",
                }
            ],
        }

    return planner


def test_domain_config_is_runtime_only_and_planners_are_gpt5() -> None:
    config = load_domain_generation_config(
        DOMAIN_CONFIG, repository_root=ROOT
    )

    assert config.workflow_profile == "complex"
    assert config.profile == WORKFLOW_PROFILES["complex"]
    assert config.models["top_entity_planning"] == PLANNING_MODEL == "gpt-5"
    assert config.models["iteration_planning"] == "gpt-5"
    raw = json.loads(DOMAIN_CONFIG.read_text(encoding="utf-8"))
    runtime_iterations = raw["runtime"]["workflow"]["iterations"]
    assert all("classes" not in iteration for iteration in runtime_iterations)
    assert all(
        "responsibilities" not in iteration for iteration in runtime_iterations
    )
    assert get_extraction_model("model:gpt-5") == "gpt-5"


def test_ontosynthesis_attached_extension_uses_agentic_artifact_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="ontomops"),
        contract={"ontology_name": "ontomops"},
        config_provenance={
            "domain_config": {"execution_channel": "ontosynthesis"}
        },
    )
    monkeypatch.setattr(
        generation_runner,
        "build_domain_generation_context",
        lambda **_: context,
    )

    generated: list[str] = []
    monkeypatch.setattr(
        generation_runner,
        "generate_deterministic_script_slice",
        lambda _: generated.append("script") or [],
    )
    monkeypatch.setattr(
        generation_runner,
        "generate_deterministic_prompt_slice",
        lambda _: generated.append("prompt") or [],
    )
    monkeypatch.setattr(
        generation_runner,
        "run_pure_llm_generation_rounds",
        lambda *_, **__: {"final_report": {"ok": True}},
    )
    summary = generation_runner.run_agentic_generation_experiment(
        ["ontomops"],
        domain_config_path=ROOT / "configs" / "domains" / "ontomops.json",
        output_root=tmp_path,
        generate_scripts=True,
        generate_prompts=True,
        llm_agent_generation=True,
    )

    report = summary["reports"][0]
    assert report["generation_mode"] == "pure_llm_unified_diff"
    assert generated == ["script", "prompt"]


@pytest.mark.parametrize("ontology_name", ["ontomops", "ontospecies"])
def test_extension_prompts_use_inherited_scope_without_generic_iter1(
    ontology_name: str, tmp_path: Path
) -> None:
    case = SIMPLE_DOMAIN_CASES[ontology_name]
    context = build_domain_generation_context(
        domain_config_path=ROOT / "configs" / "domains" / f"{ontology_name}.json",
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_simple_planner(case),
    )

    generate_deterministic_prompt_slice(context)
    prompt_names = {path.name for path in Path(context.prompts_dir).glob("*.md")}
    expected_iteration = "1" if ontology_name == "ontomops" else "2"
    assert prompt_names == {
        f"EXTRACTION_ITER_{expected_iteration}.md",
        f"KG_BUILDING_ITER_{expected_iteration}.md",
    }
    extraction_target = (
        Path(context.prompts_dir) / f"EXTRACTION_ITER_{expected_iteration}.md"
    )
    contract = pure_llm_generation._prompt_artifact_generation_contract(
        context,
        extraction_target,
    )
    assert contract["generic_pipeline_role"]["role"] == "extension_scoped_extraction"
    assert contract["tbox_scope"]["inherited_scoped_root"]["class_local"] == (
        "ChemicalSynthesis"
    )
    assert contract["tbox_scope"]["extension_focus"]["class_local"] == case["focus"]
    assert contract["runtime_binding_contract"]["allowed_slots"] == [
        "{entity_label}",
        "{entity_uri}",
    ]
    component = pure_llm_generation._materializable_prompt_component_text(
        context,
        extraction_target,
    )
    assert "{accumulated_hints}" not in component
    role_contract = pure_llm_generation._artifact_role_contract(
        extraction_target,
        contract,
    )
    role_requirements = "\n".join(role_contract["must"])
    assert "No accumulated prior-hint registry is available" in role_requirements
    assert "Treat accumulated prior hints as an identity registry" not in role_requirements
    kg_target = Path(context.prompts_dir) / f"KG_BUILDING_ITER_{expected_iteration}.md"
    kg_contract = pure_llm_generation._prompt_artifact_generation_contract(
        context,
        kg_target,
    )
    assert kg_contract["runtime_binding_contract"]["allowed_slots"] == [
        "{doi}",
        "{entity_label}",
        "{entity_uri}",
        "{enrichment_targets}",
        "{main_ontology_a_box}",
        "{paper_content}",
    ]
    assert kg_contract["runtime_binding_contract"]["handoff_channel"] == (
        "{paper_content}"
    )
    assert kg_contract["runtime_binding_contract"]["handoff_representation"] == (
        "ref-entity-relations.v1"
    )
    policy = pure_llm_generation._extension_meta_prompt_policy()
    assert "{ontosynthesis_a_box}" in policy["forbidden_runtime_slots"]
    assert "{iteration_hints}" in policy["forbidden_runtime_slots"]
    assert policy["handoff_channel"] == "{paper_content}"
    extraction_policy = pure_llm_generation._extension_meta_prompt_policy(
        extraction_target
    )
    assert extraction_policy["canonical_runtime_slots"] == [
        "{entity_label}",
        "{entity_uri}",
    ]
    extraction_target.write_text(
        "Scope: {entity_label} ({entity_uri})",
        encoding="utf-8",
    )
    assert validate_prompt_runtime_bindings(extraction_target, context)["ok"]
    kg_target.write_text(
        "{doi}\n{entity_label}\n{entity_uri}\n"
        "{enrichment_targets}\n{main_ontology_a_box}\n{paper_content}",
        encoding="utf-8",
    )
    assert validate_prompt_runtime_bindings(kg_target, context)["ok"]
    stage_failures, _, _ = _stage_artifact_contract_report(
        context,
        [kg_target.relative_to(Path(context.output_root)).as_posix()],
    )
    assert not any("top creator" in failure for failure in stage_failures)
    kg_target.write_text("{iteration_hints}", encoding="utf-8")
    invalid_binding = validate_prompt_runtime_bindings(kg_target, context)
    assert not invalid_binding["ok"]
    assert "iteration_hints" in " ".join(invalid_binding["failures"])


@pytest.mark.parametrize("ontology_name", sorted(SIMPLE_DOMAIN_CASES))
def test_simple_domain_configs_compile_with_expected_runtime_binding(
    ontology_name: str,
    tmp_path: Path,
) -> None:
    case = SIMPLE_DOMAIN_CASES[ontology_name]
    config_path = ROOT / "configs" / "domains" / f"{ontology_name}.json"
    config = load_domain_generation_config(config_path, repository_root=ROOT)

    assert config.workflow_profile == "simple"
    assert config.profile == WORKFLOW_PROFILES["simple"]
    if case["role"] == "extension":
        assert config.execution_profile == "simple_extension"
        assert "top_entity_planning" not in config.models
        assert config.models["extension_focus_planning"] == PLANNING_MODEL
        assert (ROOT / "data" / "ontologies" / "ontosynthesis.ttl").resolve() in (
            config.supporting_tboxes
        )
        assert config.agents["top_entity_extraction"] is False
        assert config.execution_channel == "ontosynthesis"
        assert (
            config.runtime["binding"]["upstream_scope"]["entity_source"]
            == "mcp_run/iter1_top_entities.json"
        )
    else:
        assert config.execution_profile == "simple_main"
        assert config.models["top_entity_planning"] == PLANNING_MODEL

    output_root = tmp_path / ontology_name
    context = build_domain_generation_context(
        domain_config_path=config_path,
        output_root=output_root,
        repository_root=ROOT,
        write_files=True,
        planner=_simple_planner(case),
    )

    assert context.ontology.role == case["role"]
    expected_top = (
        "ChemicalSynthesis" if case["role"] == "extension" else case["focus"]
    )
    assert context.contract["top_entity"]["class_local"] == expected_top
    assert context.contract["top_entity"]["class_iri"]
    if case["role"] == "extension":
        assert context.contract["top_entity"]["owned_by_extension"] is False
        assert context.contract["top_entity"]["main_pass_reuses_scoped_root"] is True
        assert (
            context.contract["top_entity"]["inherited_from_ontology"]
            == "ontosynthesis"
        )
        assert context.contract["extension_focus"]["class_local"] == case["focus"]
    else:
        assert "extension_focus" not in context.contract
    iterations = context.iteration_blueprint["iterations"]
    assert [item["iteration_number"] for item in iterations] == [2]
    assert iterations[0]["mcp_tools"] == case["tools"]
    assert {
        item["local"] for item in iterations[0]["semantic_scope"]["classes"]
    } == _expected_primary_classes(context, case)
    assert {
        item["local"]
        for item in iterations[0]["semantic_scope"]["object_properties"]
    } == set(case["properties"])
    relationship_contract = Path(context.scripts_dir) / "_relationship_contract.json"
    runtime_contract = json.loads(relationship_contract.read_text(encoding="utf-8"))
    assert runtime_contract["top_entity"]["class_local"] == expected_top
    assert runtime_contract["top_entity"]["class_iri"] == context.contract[
        "top_entity"
    ]["class_iri"]

    adapter = json.loads(
        (
            output_root
            / "derived_inputs"
            / ontology_name
            / "meta_task_adapter.json"
        ).read_text(encoding="utf-8")
    )
    if case["role"] == "extension":
        assert adapter["ontologies"]["main"]["name"] == "ontosynthesis"
        assert [item["name"] for item in adapter["ontologies"]["extensions"]] == [
            ontology_name
        ]
        enrichment = adapter["ontologies"]["extensions"][0]["runtime_policies"][
            "enrichment_target"
        ]
        assert enrichment["path"]
        assert enrichment["target_class_iri"] == context.contract["extension_focus"][
            "class_iri"
        ]
        assert enrichment["query_file"] == (
            f"sparqls/{ontology_name}/enrichment_target.sparql"
        )
    else:
        assert adapter["ontologies"]["main"]["name"] == ontology_name
        assert adapter["ontologies"]["extensions"] == []
        runtime_policies = adapter["ontologies"]["main"]["runtime_policies"]
        assert runtime_policies["top_entity_extraction"][
            "count_lines_starting_with"
        ] == [expected_top]
        assert runtime_policies["iter1_top_entity_kg"]["prompt_rules"][
            "top_level_entity_name"
        ] == expected_top
    active_entries = (
        [adapter["ontologies"]["main"]]
        if case["role"] == "main"
        else adapter["ontologies"]["extensions"]
    )
    assert all(
        not Path(item["ttl_file"]).is_absolute() for item in active_entries
    )


def test_check_manifest_tracks_tbox_and_external_contract_changes() -> None:
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="synthetic"),
        parsed={
            "classes": {
                "Zulu": {"iri": "urn:test:Zulu"},
                "Alpha": {"iri": "urn:test:Alpha"},
            },
            "properties": {},
        },
        contract={
            "external_class_creators": [
                {
                    "class_local": "ExternalB",
                    "class_iri": "urn:test:ExternalB",
                    "check_tool_name": "check_existing_ExternalB",
                },
                {
                    "class_local": "ExternalA",
                    "class_iri": "urn:test:ExternalA",
                    "check_tool_name": "check_existing_ExternalA",
                },
            ]
        },
    )

    expected = [
        "check_ordered_members",
        "check_existing_Alpha",
        "check_existing_Zulu",
        "check_existing_ExternalB",
        "check_existing_ExternalA",
    ]
    assert _existing_entity_check_manifest(context) == expected
    checks_contract = _artifact_generation_contract(
        context, Path("synthetic_creation_checks.py")
    )
    assert checks_contract["expected_public_manifest"] == expected
    violation_schema = checks_contract["ordered_check_contract"]["output_schema"][
        "violations"
    ]
    assert violation_schema["required_fields"] == ["code"]
    assert violation_schema["code_field"]["name"] == "code"
    assert "violation_code" in violation_schema["forbidden_discriminator_aliases"]

    role = _artifact_role_contract(Path("synthetic_creation_checks.py"))
    guidance = _artifact_generation_guidance(
        Path("synthetic_creation_checks.py")
    )
    assert any(
        "required discriminator is exactly `code`" in rule for rule in role["must"]
    )
    assert any("`violation_code`" in rule for rule in role["must_not"])
    assert "exact discriminator key `code`" in guidance
    assert "never emit `violation_code`" in guidance

    context.parsed["classes"]["Beta"] = {"iri": "urn:test:Beta"}
    context.contract["external_class_creators"].pop(0)
    changed_expected = [
        "check_ordered_members",
        "check_existing_Alpha",
        "check_existing_Beta",
        "check_existing_Zulu",
        "check_existing_ExternalA",
    ]
    assert _existing_entity_check_manifest(context) == changed_expected
    assert _artifact_generation_contract(
        context, Path("synthetic_creation_checks.py")
    )["expected_public_manifest"] == changed_expected


def test_ordered_check_contract_locks_ancestor_map_without_class_names() -> None:
    context = SimpleNamespace(
        ontology=SimpleNamespace(name="synthetic"),
        parsed={
            "classes": {
                "ConcreteMember": {
                    "iri": "urn:test:ConcreteMember",
                    "parent_classes": ["AbstractMember"],
                },
                "AbstractMember": {"iri": "urn:test:AbstractMember"},
                "Unrelated": {"iri": "urn:test:Unrelated"},
            },
            "properties": {},
        },
        contract={
            "ordered_member_profile": {
                "ordered_member_classes": [
                    "AbstractMember",
                    "ConcreteMember",
                    "Unrelated",
                ],
            }
        },
    )
    mapping = _required_explicit_ancestor_types(context)
    assert mapping == {"urn:test:ConcreteMember": ["urn:test:AbstractMember"]}

    contract = _artifact_generation_contract(
        context, Path("synthetic_creation_checks.py")
    )["ordered_check_contract"]
    assert contract["required_explicit_ancestor_types"] == mapping
    assert contract["ancestor_algorithm"]["authoritative"] is True
    example = contract["ancestor_algorithm"]["generic_example"]
    assert example["required_violation_codes"] == ["missing_explicit_ancestor_type"]
    joined = " ".join(
        [
            example["setup"],
            example["invalid_member"],
            example["valid_member"],
            *example["non_violations"],
        ]
    )
    assert "ConcreteMember" not in joined
    assert "AbstractMember" not in joined
    assert "SynthesisStep" not in joined

    role = _artifact_role_contract(Path("synthetic_creation_checks.py"))
    assert any("no ontology class names" in rule for rule in role["must"])
    assert any("family membership" in rule for rule in role["must_not"])
    guidance = _artifact_generation_guidance(Path("synthetic_creation_checks.py"))
    assert "no ontology class names" in guidance
    assert "required_explicit_ancestor_types" in guidance


def test_domain_config_rejects_semantic_overrides(tmp_path: Path) -> None:
    raw = json.loads(DOMAIN_CONFIG.read_text(encoding="utf-8"))
    raw["top_entity"] = "ChemicalSynthesis"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden semantic field"):
        load_domain_generation_config(path, repository_root=ROOT)


def test_domain_config_rejects_tbox_derived_runtime_contracts(tmp_path: Path) -> None:
    raw = json.loads(DOMAIN_CONFIG.read_text(encoding="utf-8"))
    raw["runtime"]["required_link_bindings"] = [
        {
            "predicate_iri": "https://example.test/retrievedFrom",
            "identity_slot": "{doi}",
            "materialization_iteration": 1,
        }
    ]
    path = tmp_path / "derived-in-domain.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden semantic field"):
        load_domain_generation_config(path, repository_root=ROOT)


def test_semantic_ordered_slot_without_enrichment_requires_closed_ledger(
    tmp_path: Path,
) -> None:
    raw = json.loads(DOMAIN_CONFIG.read_text(encoding="utf-8"))
    raw["runtime"]["workflow"]["iterations"][1]["pre_extraction_validation"][
        "closed_ledger"
    ]["enabled"] = False
    path = tmp_path / "open-ledger.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="without enrichment requires"):
        load_domain_generation_config(path, repository_root=ROOT)


def test_semantic_profile_rejects_runtime_enrichment_mismatch(
    tmp_path: Path,
) -> None:
    raw = json.loads(DOMAIN_CONFIG.read_text(encoding="utf-8"))
    raw["runtime"]["workflow"]["iterations"][1]["enrichment"] = [
        {"model_config_key": "iter3_1_enrichment"}
    ]
    path = tmp_path / "unexpected-enrichment.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match semantic profile slots"):
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
    assert [item["use_agent"] for item in iterations] == [True, True, False]
    assert iterations[1]["has_pre_extraction"] is True
    assert iterations[1]["pre_extraction_model_key"] == "advanced_model"
    assert iterations[1]["pre_extraction_validation"] == {
        "closed_ledger": {
            "enabled": True,
            "audit_format_retries": 3,
            "fail_open_on_audit_format_error": True,
            "nonblocking_after_semantic_exhaustion": True,
        }
    }
    assert iterations[1]["hint_representation"] == "semantic-text.v1"
    assert iterations[0]["hint_representation"] == "semantic-text.v1"
    assert iterations[2]["hint_representation"] == "semantic-text.v1"
    assert iterations[1]["linked_materialization_classes"] == ["ChemicalInput"]
    assert "ordered_member_contracts" not in json.loads(
        DOMAIN_CONFIG.read_text(encoding="utf-8")
    )["runtime"]
    domain_runtime = json.loads(DOMAIN_CONFIG.read_text(encoding="utf-8"))["runtime"]
    assert "required_link_bindings" not in domain_runtime
    assert domain_runtime["external_identity_bindings"] == [
        {
            "identity_slot": "{doi}",
            "target_class_locals": ["Document", "BibliographicResource"],
            "materialization_iteration": 1,
        }
    ]
    assert context.contract["runtime_required_link_bindings"] == [
        {
            "predicate_iri": (
                "https://www.theworldavatar.com/kg/OntoSyn/retrievedFrom"
            ),
            "identity_slot": "{doi}",
            "materialization_iteration": 1,
        }
    ]
    assert iterations[0]["extraction_validation"]["required_executed_tool_groups"][0][
        "name"
    ] == "chemical_identity_lookup"
    iter3_closure = {
        item["local"]
        for item in iterations[1]["semantic_scope"]["property_closure"]
    }
    assert {
        "hasAlternativeNames",
        "hasAmount",
        "hasChemicalDescription",
        "hasChemicalFormula",
        "hasPurity",
    } <= iter3_closure
    assert "sub_iterations" not in iterations[1]
    assert "enrichment_focus" not in iterations[1]
    assert iterations[0]["extraction_mcp_set_name"] == "chemistry.json"
    assert iterations[0]["extraction_mcp_tools"] == [
        "pubchem",
        "enhanced_websearch",
        "ccdc",
    ]
    iter2_component = pure_llm_generation._materializable_prompt_component_text(
        context,
        Path(context.prompts_dir) / "EXTRACTION_ITER_2.md",
    )
    assert "External MCP Use Contract (mechanically injected)" in iter2_component
    assert "`pubchem`" in iter2_component
    assert "`enhanced_websearch`" in iter2_component
    assert "`ccdc`" in iter2_component
    assert "`chemical_identity_lookup`" in iter2_component
    assert "`search_pubchem_by_name`" in iter2_component
    assert "for every applicable in-scope entity occurrence" in iter2_component
    assert "with arguments identifying that entity" in iter2_component
    iter1_role = pure_llm_generation._generic_prompt_pipeline_role(
        Path(context.prompts_dir) / "KG_BUILDING_ITER_1.md"
    )
    assert "exact output of EXTRACTION_ITER_1" in iter1_role["input_semantics"]
    assert "Never hardcode the T-Box class name" in iter1_role["required_sequence"][0]
    assert iterations[2]["inputs"] == {"source": "stitched_paper"}
    assert "DocumentContext" in {
        item["local"] for item in iterations[0]["semantic_scope"]["classes"]
    }
    assert "retrievedFrom" in {
        item["local"]
        for item in iterations[0]["semantic_scope"]["object_properties"]
    }
    iter2_slice = _prompt_tbox_slice(context, iterations[0])
    assert "DocumentContext" in iter2_slice["classes"]
    assert "retrievedFrom" in iter2_slice["properties"]
    assert "hasSynthesisStep" not in iter2_slice["properties"]
    iter4_component = pure_llm_generation._materializable_prompt_component_text(
        context,
        Path(context.prompts_dir) / "EXTRACTION_ITER_4.md",
    )
    assert "Semantic entity class `ChemicalSynthesis`" in iter4_component or (
        "Entity class `ChemicalSynthesis`" in iter4_component
    )
    assert "`hasYield`" in iter4_component
    kg1_contract = pure_llm_generation._prompt_artifact_generation_contract(
        context,
        Path(context.prompts_dir) / "KG_BUILDING_ITER_1.md",
    )
    assert len(kg1_contract["pipeline_required_link_contracts"]) == 1
    required_binding = kg1_contract["pipeline_required_link_contracts"][0]
    assert required_binding["predicate_iri"] == (
        "https://www.theworldavatar.com/kg/OntoSyn/retrievedFrom"
    )
    assert required_binding["identity_source_slot"] == "{doi}"
    assert required_binding["materialization_iteration"] == 1
    kg1_tools = kg1_contract["agent_tool_contract"]
    assert "create_Document" in {
        item["name"] for item in kg1_tools["creator_tools"]
    }
    assert "add_retrievedFrom" in {
        item["name"] for item in kg1_tools["relationship_tools"]
    }
    assert "check_existing_Document" in {
        item["name"] for item in kg1_tools["check_tools"]
    }
    kg4_contract = pure_llm_generation._prompt_artifact_generation_contract(
        context,
        Path(context.prompts_dir) / "KG_BUILDING_ITER_4.md",
    )
    assert kg4_contract["pipeline_required_link_contracts"] == []
    kg4_tools = kg4_contract["agent_tool_contract"]
    assert "create_Document" not in {
        item["name"] for item in kg4_tools["creator_tools"]
    }
    assert "add_retrievedFrom" not in {
        item["name"] for item in kg4_tools["relationship_tools"]
    }
    assert "check_existing_Document" not in {
        item["name"] for item in kg4_tools["check_tools"]
    }
    iter3_target = Path(context.prompts_dir) / "EXTRACTION_ITER_3.md"
    iter3_contract = pure_llm_generation._prompt_artifact_generation_contract(
        context,
        iter3_target,
    )
    assert "{accumulated_hints}" in (
        iter3_contract["runtime_binding_contract"]["allowed_slots"]
    )
    assert "{accumulated_hints}" not in (
        iter3_contract["runtime_binding_contract"]["llm_authored_slots"]
    )
    assert iter3_contract["runtime_binding_contract"][
        "mechanically_injected_slots"
    ] == ["{accumulated_hints}"]
    assert iter3_contract["representation_policy"][
        "required_hint_representation"
    ] == "semantic-text.v1"
    iter3_guidance = _artifact_generation_guidance(iter3_target)
    assert "semantic-text.v1 requires only its natural-language SEMANTIC_HINTS_V1 ledger" in iter3_guidance
    assert "all acceptable" not in iter3_guidance
    iter3_role = _artifact_role_contract(iter3_target, iter3_contract)
    iter3_must = " ".join(iter3_role["must"])
    assert "SEMANTIC_HINTS_V1" in iter3_must
    assert "short subclass label" in iter3_must
    assert "sequence position" in iter3_must
    assert "hasOrder" not in iter3_must
    assert "Do not require the heading" in iter3_must
    assert "exactly one dense natural-language paragraph" not in iter3_must
    iter3_component = pure_llm_generation._materializable_prompt_component_text(
        context,
        iter3_target,
    )
    assert "Semantic Hint Contract (mechanically injected)" in iter3_component
    assert "SEMANTIC_HINTS_V1" in iter3_component
    assert "ChemicalInput" in iter3_component
    assert "`hasAlternativeNames`" in iter3_component
    assert "`hasAmount`" in iter3_component
    assert "natural-language ledger headed exactly" in iter3_component
    assert "short subclass label" in iter3_component
    assert "sequence position" in iter3_component
    assert "record hasOrder" not in iter3_component
    assert "exactly one dense natural-language paragraph" not in iter3_component
    assert "`<SubclassLocal> (Order: <n>)`" in iter3_component
    assert "Do not emit JSON, RDF, refs, IRIs" in iter3_component
    assert "subject_ref" not in iter3_component
    assert "object_ref" not in iter3_component
    assert "one JSON object" not in iter3_component

    manifest = json.loads(
        (
            Path(context.ontology_structure_dir)
            / "domain_artifact_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["planning_model"] == "gpt-5"
    assert manifest["schema_version"] == "domain-artifact-manifest.v1"
    accepted = manifest["semantic_decisions"]
    assert "enrichment_focus" not in accepted["assignments"]
    assert all(
        "enrichment_focus" not in item
        for item in accepted["iteration_decomposition"]["iterations"]
    )
    domain_artifacts = json.dumps(
        {
            "accepted": accepted,
            "blueprint": json.loads(
                (
                    tmp_path
                    / "derived_inputs"
                    / "ontosynthesis"
                    / "iteration_blueprint.json"
                ).read_text(encoding="utf-8")
            ),
            "compiled": context.iteration_blueprint,
        }
    )
    assert "iter3.1" not in domain_artifacts
    assert "iter3.2" not in domain_artifacts
    assert context.config_provenance["boundary"]["manual_inputs"] == [
        "tbox_bundle",
        "domain_config",
    ]

    written = generate_deterministic_script_slice(
        context
    ) + generate_deterministic_prompt_slice(context)
    assert written
    generated_plan = json.loads(
        (
            tmp_path / "iterations" / "ontosynthesis" / "iterations.json"
        ).read_text(encoding="utf-8")
    )
    assert [item["iteration_number"] for item in generated_plan["iterations"]] == [
        2,
        3,
        4,
    ]
    assert (
        tmp_path
        / "sparqls"
        / "ontosynthesis"
        / "top_entity_parsing.sparql"
    ).read_text(encoding="utf-8").find(
        "<https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis>"
    ) > 0
    iter2_prompt = (
        tmp_path / "prompts" / "ontosynthesis" / "EXTRACTION_ITER_2.md"
    ).read_text(encoding="utf-8")
    assert "SEMANTIC_HINTS_V1" in iter2_prompt
    assert "Reusable Entity Label Contract:" in iter2_prompt
    assert "Do not emit JSON, RDF, refs, IRIs" in iter2_prompt
    assert "Hint Schema: ref-entity-relations.v1" not in iter2_prompt
    assert '"subject_ref"' not in iter2_prompt
    assert "Semantic Hint Contract (mechanically injected)" in iter2_component
    assert "Do not emit JSON, RDF, refs, IRIs" in iter2_component
    assert "DocumentContext" in iter2_component
    assert "The label must be independent of the Current Target Entity" in iter2_component or (
        "The label must be independent of the Current Target Entity" in iter2_prompt
    )

    entities_source = (
        tmp_path
        / "scripts"
        / "ontosynthesis"
        / "ontosynthesis_creation_entities.py"
    ).read_text(encoding="utf-8")
    entities_tree = ast.parse(entities_source)
    allowed_owned_labels = {
        str(contract.get("public_tool") or ""): {
            str(edge.get("label_parameter") or "")
            for edge in contract.get("required_edges") or []
            if edge.get("target_resolution") == "same_operation_create"
        }
        for contract in _owned_entity_tool_contracts(context)
    }
    for node in entities_tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("create_"):
            parameter_names = [
                argument.arg
                for argument in [*node.args.args, *node.args.kwonlyargs]
            ]
            assert not (
                {
                    name for name in parameter_names if name.endswith("_label")
                }
                - allowed_owned_labels.get(node.name, set())
            ), node.name
    assert "_split_label_scalar" not in entities_source
    assert "embedded_scalar" not in entities_source

    iter2_kg_prompt = (
        tmp_path / "prompts" / "ontosynthesis" / "KG_BUILDING_ITER_2.md"
    ).read_text(encoding="utf-8")
    assert "SEMANTIC_HINTS_V1 natural-language semantic ledger" in iter2_kg_prompt
    assert "Do not require JSON entities/relations" in iter2_kg_prompt
    assert "runtime map from each hint `ref`" not in iter2_kg_prompt
    assert "Atomic Create Tool Contract:" in iter2_kg_prompt
    iter2_scope = generated_plan["iterations"][0]["semantic_scope"]
    expected_classes = ", ".join(
        item["local"] for item in iter2_scope["classes"]
    )
    expected_properties = ", ".join(
        item["local"] for item in iter2_scope["object_properties"]
    )
    assert f"Iteration-owned classes: [{expected_classes}]" in iter2_kg_prompt
    assert (
        f"Iteration-owned object_properties: [{expected_properties}]"
        in iter2_kg_prompt
    )
    assert "Entity class `ChemicalInput`" in iter2_kg_prompt
    assert "Relation `hasChemicalInput`" in iter2_kg_prompt
    assert "Entity class `Add`" not in iter2_kg_prompt


def test_ontosynthesis_blueprint_uses_single_main_step_extraction() -> None:
    blueprint = json.loads(
        (
            ROOT
            / "configs"
            / "meta_task"
            / "ontosynthesis_iterations_blueprint.json"
        ).read_text(encoding="utf-8")
    )
    iter3 = next(
        item for item in blueprint["iterations"] if item["iteration_number"] == 3
    )
    assert "sub_iterations" not in iter3


def test_iteration_assignment_does_not_call_llm(tmp_path: Path) -> None:
    iteration_attempts = 0

    def bad_planner(model: str, prompt: str) -> dict:
        nonlocal iteration_attempts
        if "Select the single top entity class" in prompt:
            return _planner(model, prompt)
        iteration_attempts += 1
        raise AssertionError("iteration ownership must not be delegated to the LLM")

    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=False,
        planner=bad_planner,
    )
    assert iteration_attempts == 0
    assert [item["iteration_number"] for item in context.iteration_blueprint["iterations"]] == [
        2,
        3,
        4,
    ]
    audit = json.loads(
        (
            tmp_path
            / "semantic_planning"
            / "ontosynthesis"
            / "deterministic_iteration_ownership.json"
        ).read_text(encoding="utf-8")
    )
    assert audit["validation"]["ok"] is True
    assert audit["candidate"]["ownership_sha256"]


def test_property_only_remainder_scope_is_valid(tmp_path: Path) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=False,
        planner=_planner,
    )
    remainder = context.iteration_blueprint["iterations"][2]
    assert remainder["responsibilities"]["classes"] == []
    assert remainder["responsibilities"]["object_properties"]


@pytest.mark.parametrize("ontology_name", ["ontomops", "ontospecies"])
def test_extension_runtime_iterations_keep_compiled_semantic_scope(
    ontology_name: str,
    tmp_path: Path,
) -> None:
    case = SIMPLE_DOMAIN_CASES[ontology_name]
    config_path = ROOT / "configs" / "domains" / f"{ontology_name}.json"
    context = build_domain_generation_context(
        domain_config_path=config_path,
        output_root=tmp_path / ontology_name,
        repository_root=ROOT,
        write_files=True,
        planner=_simple_planner(case),
    )

    plan = _iteration_plan(context)
    assert len(plan["iterations"]) == 1
    runtime_iteration = plan["iterations"][0]
    pipeline_number = (
        context.config_provenance.get("domain_config") or {}
    ).get("pipeline_iteration_number")
    assert runtime_iteration["iteration_number"] == pipeline_number
    assert {
        item["local"] for item in runtime_iteration["semantic_scope"]["classes"]
    } == _expected_primary_classes(context, case)
    assert {
        item["local"]
        for item in runtime_iteration["semantic_scope"]["object_properties"]
    } == set(case["properties"])

    generate_runtime_support_slice(context, iterations=plan)
    compiled_sparql = (
        Path(context.output_root)
        / "sparqls"
        / ontology_name
        / "enrichment_target.sparql"
    )
    assert compiled_sparql.is_file()
    query = compiled_sparql.read_text(encoding="utf-8")
    assert f"?{context.contract['extension_focus']['class_iri']}" not in query
    assert f"<{context.contract['extension_focus']['class_iri']}>" in query
    written = json.loads(
        (
            Path(context.output_root)
            / "iterations"
            / ontology_name
            / "iterations.json"
        ).read_text(encoding="utf-8")
    )
    assert written["iterations"][0]["semantic_scope"]["classes"]
    assert written["iterations"][0]["responsibilities"]["classes"]


def test_prompt_tbox_slice_rejects_empty_semantic_scope() -> None:
    context = SimpleNamespace(
        parsed={"classes": {"Child": {"iri": "https://example.test/Child", "parent_classes": []}}, "properties": {}},
        contract={"top_entity": {"class_local": "Child"}},
    )
    with pytest.raises(ValueError, match="non-empty semantic_scope"):
        _prompt_tbox_slice(
            context,
            {
                "iteration_number": 3,
                "semantic_scope": {"classes": [], "object_properties": []},
                "responsibilities": {"classes": [], "object_properties": []},
            },
        )


def test_prompt_tbox_slice_accepts_property_only_scope() -> None:
    context = SimpleNamespace(
        parsed={
            "classes": {
                "Root": {
                    "iri": "https://example.test/Root",
                    "parent_classes": [],
                }
            },
            "properties": {
                "hasMetric": {
                    "iri": "https://example.test/hasMetric",
                    "kind": "object",
                    "domains": ["Root"],
                    "range": "ExternalMetric",
                }
            },
        },
        contract={"top_entity": {"class_local": "Root"}},
    )
    result = _prompt_tbox_slice(
        context,
        {
            "iteration_number": 4,
            "semantic_scope": {
                "classes": [],
                "object_properties": [
                    {
                        "local": "hasMetric",
                        "iri": "https://example.test/hasMetric",
                    }
                ],
            },
            "responsibilities": {
                "classes": [],
                "object_properties": ["hasMetric"],
            },
        },
    )
    assert result["classes"] == {}
    assert set(result["properties"]) == {"hasMetric"}


def test_prompt_tbox_slice_includes_materialization_class_datatypes_only() -> None:
    context = SimpleNamespace(
        parsed={
            "classes": {
                "Host": {
                    "iri": "https://example.test/Host",
                    "parent_classes": [],
                    "comment": "owned host",
                },
                "Payload": {
                    "iri": "https://example.test/Payload",
                    "parent_classes": [],
                    "comment": "materialized payload",
                },
            },
            "properties": {
                "hasPayload": {
                    "iri": "https://example.test/hasPayload",
                    "kind": "object",
                    "domains": ["Host"],
                    "range": "Payload",
                },
                "hasAlias": {
                    "iri": "https://example.test/hasAlias",
                    "kind": "datatype",
                    "domains": ["Payload"],
                    "range": "string",
                },
                "hasParentPayload": {
                    "iri": "https://example.test/hasParentPayload",
                    "kind": "object",
                    "domains": ["Root"],
                    "range": "Payload",
                },
            },
        },
        contract={"top_entity": {"class_local": "Root"}},
    )
    result = _prompt_tbox_slice(
        context,
        {
            "iteration_number": 3,
            "linked_materialization_classes": ["Payload"],
            "semantic_scope": {
                "classes": [
                    {"local": "Host", "iri": "https://example.test/Host"}
                ],
                "object_properties": [
                    {
                        "local": "hasPayload",
                        "iri": "https://example.test/hasPayload",
                    }
                ],
            },
            "responsibilities": {
                "classes": ["Host"],
                "object_properties": ["hasPayload"],
            },
        },
    )
    assert "Host" in result["classes"]
    assert "Payload" in result["classes"]
    assert "hasAlias" in result["properties"]
    assert "hasParentPayload" not in result["properties"]


def test_deterministic_assignment_writes_canonical_audit(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=False,
        planner=_planner,
    )

    assert [item["iteration_number"] for item in context.iteration_blueprint["iterations"]] == [
        2,
        3,
        4,
    ]
    planning_dir = tmp_path / "semantic_planning" / "ontosynthesis"
    audit = json.loads(
        (planning_dir / "deterministic_iteration_ownership.json").read_text(
            encoding="utf-8"
        )
    )
    assert audit["validation"]["ok"] is True
    assert audit["candidate"]["ownership_provenance"]["schema_version"] == (
        "iteration-ownership.v1"
    )
    assert (planning_dir / "accepted_semantic_plan.json").is_file()


def test_step_typing_integrity_propagates_through_planner_and_prompt_contract(
    tmp_path: Path,
) -> None:
    parsed = parse_ontology_ttl(str(ROOT / "data" / "ontologies" / "ontosynthesis.ttl"))
    captured_planner_prompts: list[str] = []

    def top_planner(model: str, prompt: str) -> dict:
        assert model == PLANNING_MODEL
        captured_planner_prompts.append(prompt)
        return {
            "class_local": "ChemicalSynthesis",
            "rationale": "The class organizes source-supported procedures.",
            "evidence": ["ChemicalSynthesis", "hasSynthesisStep"],
        }

    plan_top_entity_semantics(parsed=parsed, planner=top_planner)

    assert captured_planner_prompts
    planner_prompt = captured_planner_prompts[0]
    assert '"integrity_annotations"' not in planner_prompt
    assert "Do not infer Stir from mixing" in planner_prompt

    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    iteration = context.iteration_blueprint["iterations"][1]
    target = Path(context.prompts_dir) / "PRE_EXTRACTION_ITER_3.md"
    contract = _prompt_artifact_generation_contract(context, target)
    assert contract["representation_policy"]["required_hint_representation"] == (
        "closed-ledger.v1"
    )
    assert contract["accumulated_prior_hint_representations"] == [
        {
            "iteration_number": 2,
            "hint_representation": "semantic-text.v1",
        }
    ]
    assert "{accumulated_hints}" in contract["runtime_binding_contract"][
        "llm_authored_slots"
    ]
    step_decision = next(
        item
        for item in contract["subclass_decision_contract"]["decision_points"]
        if item["parent_class_local"] == "SynthesisStep"
    )
    candidate_names = {
        item["class_local"] for item in step_decision["candidate_subclasses"]
    }
    assert {"Add", "Stir", "HeatChill", "Filter", "Separate"} <= candidate_names
    stir_candidate = next(
        item
        for item in step_decision["candidate_subclasses"]
        if item["class_local"] == "Stir"
    )
    assert "Do not infer Stir from mixing" in stir_candidate["comment"]
    lexical_quantity_locals = {
        item["predicate_local"]
        for item in contract["lexical_quantity_hint_contract"]["properties"]
    }
    assert {"hasStepDuration", "hasTargetTemperature"} <= lexical_quantity_locals
    assert any(
        "lexemes are the pipeline interchange" in rule
        for rule in contract["lexical_quantity_hint_contract"]["rules"]
    )
    pre_candidate_types = set(
        contract["pre_extraction_candidate_type_contract"][
            "allowed_candidate_types"
        ]
    )
    assert {
        "ChemicalInput",
        "Add",
        "Stir",
        "HeatChill",
        "Evaporate",
        "Sonicate",
        "Transfer",
        "Separate",
        "Filter",
        "Dry",
    } == pre_candidate_types
    assert not {
        "SynthesisStep",
        "Vessel",
        "Equipment",
    } & pre_candidate_types
    assert any(
        "one contiguous substring" in rule
        and "shared sentence" in rule
        for rule in contract["pre_extraction_candidate_type_contract"]["rules"]
    )
    assert "SynthesisStep" not in set(
        _pre_extraction_candidate_type_contract(
            context,
            contract["tbox_scope"],
            {"decision_points": []},
        )["allowed_candidate_types"]
    )
    narrow_candidate_types = set(
        _pre_extraction_candidate_type_contract(
            context,
            {
                "classes": {
                    "ChemicalInput": context.parsed["classes"]["ChemicalInput"],
                },
                "properties": {},
            },
            {"decision_points": []},
        )["allowed_candidate_types"]
    )
    assert pre_candidate_types == narrow_candidate_types

    deterministic_pre = _pre_extraction_prompt(context, iteration)
    assert "T-Box-Derived Scope:" in deterministic_pre
    assert "Subclass Decision Checklist:" in deterministic_pre
    assert "Do not infer Stir from mixing" in deterministic_pre
    assert "Slow evaporation that produces crystals remains Evaporate" in deterministic_pre

    extraction_contract = _prompt_artifact_generation_contract(
        context,
        Path(context.prompts_dir) / "EXTRACTION_ITER_3.md",
    )
    projected = {
        item["subclass_local"]: item
        for item in extraction_contract["subclass_comment_projection"]["subclasses"]
        if item["parent_class_local"] == "SynthesisStep"
    }
    expected_comment_phrases = {
        "Add": "Never split one introduction by identity, amount, or role.",
        "Transfer": "does not retroactively create Transfer",
        "HeatChill": "A controlled heating or cooling transition",
        "Crystallize": "A crystalline result, crystal formation or growth",
    }
    for class_local, phrase in expected_comment_phrases.items():
        assert phrase in projected[class_local]["comment"]
        assert phrase in extraction_contract["tbox_scope"]["classes"][class_local][
            "comment"
        ]
    assert extraction_contract["subclass_comment_projection"]["requirements"]
    extraction_task = pure_llm_generation._generation_task(
        context=context,
        report={"ok": True, "failures": []},
        round_index=1,
        generate_scripts=False,
        generate_prompts=True,
        target=Path(context.prompts_dir) / "EXTRACTION_ITER_3.md",
    )
    for phrase in expected_comment_phrases.values():
        assert phrase in extraction_task
    assert "formal OWL/RDFS structure" in extraction_task


def test_iter3_prompt_artifacts_match_projected_subclass_integrity_rules(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    generated = {
        Path(path).name: Path(path)
        for path in generate_deterministic_prompt_slice(context)
    }
    extraction_target = Path(context.prompts_dir) / "EXTRACTION_ITER_3.md"
    prompt_contract = _prompt_artifact_generation_contract(
        context,
        extraction_target,
    )
    projected_subclasses = (
        prompt_contract["subclass_comment_projection"]["subclasses"]
    )
    expected_comments = {
        str(subclass["comment"])
        for subclass in projected_subclasses
        if str(subclass.get("comment") or "").strip()
        and str(subclass.get("subclass_local") or "") != "Crystallize"
    }
    assert expected_comments

    generated_pre = generated["PRE_EXTRACTION_ITER_3.md"].read_text(
        encoding="utf-8"
    )
    generated_extraction = generated["EXTRACTION_ITER_3.md"].read_text(
        encoding="utf-8"
    ).replace("\r\n", "\n")
    assert "integrity_annotations" not in generated_extraction
    assert "【Warning】" in generated_extraction
    assert "Do not infer Stir from mixing" in generated_extraction

    materializable_generated = generated[
        "EXTRACTION_ITER_3.materializable.inc"
    ].read_text(encoding="utf-8").replace("\r\n", "\n")
    for comment in expected_comments:
        assert comment.replace("\r\n", "\n") in materializable_generated
    assert "【Warning】" in generated_pre
    assert "Crystallize" in generated_pre and "Transfer" in generated_pre
    assert "mixed/combined" in generated_pre.lower()
    assert "already-existing" in generated_pre.lower()


def test_prompt_meta_contract_remains_free_of_ontosynthesis_step_rules() -> None:
    source = (
        ROOT
        / "src"
        / "agents"
        / "scripts_and_prompts_generation"
        / "pure_llm_generation.py"
    ).read_text(encoding="utf-8")

    assert "subclass_decision_contract" in source
    assert "lexical_quantity_hint_contract" in source
    assert "pre_extraction_candidate_type_contract" in source
    assert "generation_contract.tbox_scope" in source
    assert "lexical→fixed-quantity-creator→" in source
    assert "datatype_properties[P]" in source
    assert "`<predicate_local>: <lexeme>` line" in source
    assert "Permit only exact lexical-quantity and semantic-scalar interchange lines after the" not in source
    assert "append lexical-quantity or semantic-scalar lines after the paragraph" not in source
    assert "For enrichment iterations, emit only an entities/relations patch" not in source
    assert "This artifact is an enrichment sub-iteration only" in source
    for domain_rule in (
        "generic_mixed_or_mixing_language_is_not_stirring_evidence",
        "workup_washing_solvent_must_not_be_add",
        "never_merge_explicit_heating_and_cooling_phases",
        "storage_or_contact_in_solvent_is_not_drying",
        "ChemicalInput has two occurrence layers",
        "synthesis-level ChemicalInput",
        "step-local ChemicalInput",
    ):
        assert domain_rule not in source


def test_semantic_text_extraction_accepts_0829_0901_natural_ledger() -> None:
    natural = (
        "Return only a natural-language ledger headed exactly SEMANTIC_HINTS_V1.\n"
        "Begin every occurrence with a short subclass label and record hasOrder.\n"
        "Do not require the heading form `<SubclassLocal> (Order: <n>)`.\n"
    )
    structured_0902 = (
        "Return only a ledger headed exactly SEMANTIC_HINTS_V1.\n"
        "Begin every occurrence with `<SubclassLocal> (Order: <n>)`.\n"
        "The heading parentheses must contain only Order: <n>.\n"
        "Use (<range_local>) tags and one-space-indented child bullets.\n"
        "Restate shared-scope context with (inherited global context).\n"
    )

    assert not _semantic_text_structured_ledger_expectation_failures(
        natural, "EXTRACTION_ITER_2.md"
    )
    assert _semantic_text_structured_ledger_expectation_failures(
        structured_0902, "EXTRACTION_ITER_2.md"
    )
    runner = (
        ROOT
        / "src"
        / "agents"
        / "scripts_and_prompts_generation"
        / "agentic_generation_runner.py"
    ).read_text(encoding="utf-8")
    assert "natural-language ledger headed exactly" in runner
    assert "_semantic_text_natural_ledger_rules" in runner
    assert "Do not append standalone `<predicate_local>: <lexeme>` lines" not in runner
    ledger = " ".join(pure_llm_generation._semantic_text_natural_ledger_rules())
    assert "short subclass label" in ledger


def test_kg_lexical_quantity_contract_mirrors_extraction_interchange(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    extraction = _prompt_artifact_generation_contract(
        context,
        Path(context.prompts_dir) / "EXTRACTION_ITER_4.md",
    )
    kg = _prompt_artifact_generation_contract(
        context,
        Path(context.prompts_dir) / "KG_BUILDING_ITER_4.md",
    )
    extraction_locals = {
        item["predicate_local"]
        for item in extraction["lexical_quantity_hint_contract"]["properties"]
    }
    kg_locals = {
        item["predicate_local"]
        for item in kg["lexical_quantity_hint_contract"]["properties"]
    }
    assert extraction["lexical_quantity_hint_contract"]["role"] == "extraction"
    assert kg["lexical_quantity_hint_contract"]["role"] == "kg"
    assert extraction["lexical_quantity_hint_contract"]["hint_representation"] == (
        "semantic-text.v1"
    )
    assert kg["lexical_quantity_hint_contract"]["hint_representation"] == (
        "semantic-text.v1"
    )
    assert extraction_locals
    assert extraction_locals == kg_locals
    assert any(
        "`<predicate_local>: <lexeme>` line" in rule
        for rule in extraction["lexical_quantity_hint_contract"]["rules"]
    )
    assert any(
        "standalone `P: <lexeme>` line" in rule
        and "relationship_target_contracts[P].creator_tools" in rule
        for rule in kg["lexical_quantity_hint_contract"]["rules"]
    )
    assert any(
        "Do not expect extraction to emit a quantity entity" in rule
        for rule in kg["lexical_quantity_hint_contract"]["rules"]
    )

    task = pure_llm_generation._generation_task(
        context=context,
        report={"ok": True, "failures": []},
        round_index=1,
        generate_scripts=False,
        generate_prompts=True,
        target=Path(context.prompts_dir) / "KG_BUILDING_ITER_4.md",
    )
    assert "Integrate generation_contract.lexical_quantity_hint_contract exactly" in task
    assert "owning natural-language occurrence paragraph" in task
    assert "datatype_properties[P]" not in task
    assert "relationship_target_contracts[P].creator_tools" in task
    assert '"role": "kg"' in task
    role = pure_llm_generation._artifact_role_contract(
        Path(context.prompts_dir) / "KG_BUILDING_ITER_4.md",
        generation_contract=kg,
    )
    assert any(
        "fixed-quantity-creator→add_* handoff" in item
        or "lexical→fixed-quantity-creator→" in item
        for item in role.get("must") or []
    )
    assert any(
        "Enumerate every entry in generation_contract.agent_tool_contract.creator_tools"
        in item
        for item in role.get("must") or []
    )


def test_semantic_text_ledger_rules_are_ontology_neutral() -> None:
    text = " ".join(pure_llm_generation._semantic_text_natural_ledger_rules())
    assert "hasOrder" not in text
    assert "species" not in text.lower()
    assert "sequence position" in text
    assert "semicolon-separated" in text
    assert "parallel aliases" in text
