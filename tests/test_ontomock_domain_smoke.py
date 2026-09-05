from __future__ import annotations

import json
from pathlib import Path

from src.agents.scripts_and_prompts_generation.domain_artifact_compiler import (
    build_domain_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_prompt_slice,
    generate_deterministic_script_slice,
)
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _materializable_prompt_component_text,
    _owned_entity_tool_contracts,
    _prompt_artifact_generation_contract,
    _validate_generated_prompt_hard_gates,
    _write_materializable_prompt_component,
)
from src.pipelines.main_kg_building.build import (
    _augment_kg_prompt_with_runtime_rules,
    _compiled_iteration_owned_surface,
)


ROOT = Path(__file__).resolve().parents[1]
DOMAIN_CONFIG = ROOT / "configs" / "domains" / "ontomock.json"


def _planner(model: str, prompt: str) -> dict:
    assert model == "gpt-5"
    assert "Select the single top entity class" in prompt
    return {
        "class_local": "ProcessRun",
        "rationale": "ProcessRun organizes the complete source-described workflow.",
        "evidence": ["ProcessRun", "hasAction"],
    }


def test_ontomock_compiles_complex_semantic_workflow(tmp_path: Path) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )

    assert context.contract["top_entity"]["class_local"] == "ProcessRun"
    iterations = context.iteration_blueprint["iterations"]
    assert [item["iteration_number"] for item in iterations] == [2, 3, 4]

    iter2, iter3, iter4 = iterations
    assert iter3["has_pre_extraction"] is True
    assert iter3["hint_representation"] == "semantic-text.v1"
    assert iter3["linked_materialization_classes"] == ["Input"]
    assert iter3["pre_extraction_validation"] == {
        "closed_ledger": {"enabled": True}
    }
    assert "sub_iterations" not in iter3
    assert "enrichment_focus" not in iter3
    assert iter4["inputs"] == {"source": "stitched_paper"}

    accepted = json.loads(
        (
            tmp_path
            / "semantic_planning"
            / "ontomock"
            / "accepted_semantic_plan.json"
        ).read_text(encoding="utf-8")
    )
    blueprint = json.loads(
        (
            tmp_path
            / "derived_inputs"
            / "ontomock"
            / "iteration_blueprint.json"
        ).read_text(encoding="utf-8")
    )
    semantic_artifacts = json.dumps(
        {
            "accepted": accepted,
            "blueprint": blueprint,
            "compiled": context.iteration_blueprint,
        }
    )
    assert "enrichment_focus" not in semantic_artifacts
    assert "iter3.1" not in semantic_artifacts
    assert "iter3.2" not in semantic_artifacts

    iter2_classes = set(iter2["responsibilities"]["classes"])
    iter3_classes = set(iter3["responsibilities"]["classes"])
    iter4_classes = set(iter4["responsibilities"]["classes"])
    assert {"Input", "Output", "SourceDoc", "Vendor"} <= iter2_classes
    assert {"DoStep", "DoAlt", "Tool"} <= iter3_classes
    assert "ActionBase" not in iter3_classes
    assert "ProhibitedType" not in iter2_classes | iter3_classes | iter4_classes

    assert "hasAction" in iter3["responsibilities"]["object_properties"]
    assert "usesInput" in iter3["responsibilities"]["object_properties"]
    assert "hasDuration" in iter3["responsibilities"]["object_properties"]
    assert iter4_classes == set()
    assert iter4["responsibilities"]["object_properties"] == ["hasMetric"]


def test_ontomock_semantic_component_has_no_structured_hint_protocol(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    target = Path(context.prompts_dir) / "EXTRACTION_ITER_3.md"
    contract = _prompt_artifact_generation_contract(context, target)
    component = _materializable_prompt_component_text(context, target)

    assert contract["representation_policy"]["required_hint_representation"] == (
        "semantic-text.v1"
    )
    assert "SEMANTIC_HINTS_V1" in component
    assert "one JSON object" not in component
    assert "subject_ref" not in component
    assert "object_ref" not in component
    assert "datatype_properties" not in component
    assert "natural-language ledger headed exactly" in component
    assert "short subclass label" in component
    assert "exactly one dense natural-language paragraph" not in component
    scalar_contract = {
        item["property_local"]: item
        for item in contract["semantic_scalar_output_contract"]
    }
    assert scalar_contract["hasOrder"]["natural_language_requirement"] == (
        "Include hasOrder with its complete <integer> value in the owning "
        "occurrence, either in the occurrence prose or as a "
        "property-local line. Do not drop a source-supported value."
    )
    assert scalar_contract["isEnabled"]["natural_language_requirement"] == (
        "Include isEnabled with its complete true|false value in the owning "
        "occurrence, either in the occurrence prose or as a "
        "property-local line. Do not drop a source-supported value."
    )
    serialized_contract = json.dumps(contract)
    for foreign_local in (
        "ChemicalInput",
        "ChemicalSynthesis",
        "hasAddedChemicalInput",
        "hasWashingSolvent",
        "hasSeparationSolvent",
    ):
        assert foreign_local not in component
        assert foreign_local not in serialized_contract


def test_ontomock_prompt_pipeline_passes_only_structural_hard_gates(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    generated = generate_deterministic_prompt_slice(context)

    assert generated
    for path in generated:
        target = Path(path)
        if target.suffix != ".md":
            continue
        _write_materializable_prompt_component(context, target)
        report = _validate_generated_prompt_hard_gates(target, context)
        assert report["ok"], report["failures"]


def test_prompt_gate_detaches_every_mechanically_injected_slot(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    generated = generate_deterministic_prompt_slice(context)
    target = next(
        Path(path)
        for path in generated
        if Path(path).name == "EXTRACTION_ITER_3.md"
    )
    _write_materializable_prompt_component(context, target)
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nPrior context:\n{accumulated_hints}\n",
        encoding="utf-8",
    )

    report = _validate_generated_prompt_hard_gates(target, context)

    assert report["ok"], report["failures"]
    assert report["evidence"]["detached_mechanically_injected_slots"] == [
        "{accumulated_hints}"
    ]
    prompt = target.read_text(encoding="utf-8")
    assert "{accumulated_hints}" not in prompt
    assert (
        "[runtime context supplied by deterministic companion component]" in prompt
    )


def test_prompt_gate_rejects_fixed_classification_field_schema(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    generated = generate_deterministic_prompt_slice(context)
    target = next(
        Path(path)
        for path in generated
        if Path(path).name == "EXTRACTION_ITER_3.md"
    )
    _write_materializable_prompt_component(context, target)
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nRequired fixed field: Selected class: <class-local>\n",
        encoding="utf-8",
    )

    report = _validate_generated_prompt_hard_gates(target, context)

    assert not report["ok"]
    assert "fixed classification field schema" in "\n".join(report["failures"])


def test_ontomock_iter1_uses_seeded_identity_without_root_creator(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    generated = generate_deterministic_prompt_slice(context)
    iter1 = next(Path(path) for path in generated if Path(path).name == "KG_BUILDING_ITER_1.md")
    prompt = iter1.read_text(encoding="utf-8")
    contract = _prompt_artifact_generation_contract(context, iter1)

    assert contract["generic_pipeline_role"]["role"] == "locked_top_entity_abox_binding"
    assert contract["tbox_scope"]["top_entity"]["creation_forbidden"] is True
    assert "creator_tool" not in contract["tbox_scope"]["top_entity"]
    assert "Never call a top-root creator" in prompt
    assert "create_ProcessRun" not in prompt
    assert "init_memory(doi, top_level_entity_name)" in prompt
    assert "export_memory(doi, top_level_entity_name)" in prompt
    assert "open_or_resume_memory" not in prompt
    assert "export_retained_memory" not in prompt


def test_ontomock_relationship_targets_remain_materializable_when_class_scope_empty(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    generated = generate_deterministic_prompt_slice(context)
    prompts = {Path(path).name: Path(path) for path in generated if Path(path).suffix == ".md"}

    iter3 = prompts["KG_BUILDING_ITER_3.md"].read_text(encoding="utf-8")
    iter4 = prompts["KG_BUILDING_ITER_4.md"].read_text(encoding="utf-8")
    iter4_contract = _prompt_artifact_generation_contract(
        context, prompts["KG_BUILDING_ITER_4.md"]
    )

    assert "`hasDuration` -> [Duration] (fixed_runtime_creator)" in iter3
    assert "`create_om2_quantity`" in iter3
    assert "Iteration-owned classes: []" in iter4
    assert "`hasMetric` -> [ExternalMetric] (generated_creator)" in iter4
    assert "`create_ExternalMetric`" in iter4
    assert iter4_contract["relationship_target_contracts"]["hasMetric"][
        "creator_tools"
    ] == ["create_ExternalMetric"]
    tool_contract = iter4_contract["agent_tool_contract"]
    assert {
        item["name"] for item in tool_contract["lifecycle_tools"]
    } == {"init_memory", "export_memory"}
    assert next(
        item
        for item in tool_contract["creator_tools"]
        if item["name"] == "create_ExternalMetric"
    )["exact_call_signature"] == "create_ExternalMetric(label: str)"
    assert next(
        item
        for item in tool_contract["relationship_tools"]
        if item["name"] == "add_hasMetric"
    )["exact_call_signature"].startswith("add_hasMetric(subject_iri: str")

    iter3_contract = _prompt_artifact_generation_contract(
        context, prompts["KG_BUILDING_ITER_3.md"]
    )
    creators = {
        item["name"]: item["exact_call_signature"]
        for item in iter3_contract["agent_tool_contract"]["creator_tools"]
    }
    assert creators["create_DoStep"] == (
        "create_DoStep(label: str, hasOrder: int, isEnabled: bool | None = None)"
    )
    assert creators["create_DoAlt"] == "create_DoAlt(label: str, hasOrder: int)"


def test_ontomock_creator_surface_filters_prohibited_and_abstract_parents(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    public_tools = {
        item["public_tool"] for item in _owned_entity_tool_contracts(context)
    }
    generated = generate_deterministic_script_slice(context)
    entities = next(
        Path(path) for path in generated if Path(path).name.endswith("_creation_entities.py")
    ).read_text(encoding="utf-8")

    assert "create_ProhibitedType" not in public_tools
    assert "create_ActionBase" not in public_tools
    assert "create_ExternalMetric" in public_tools
    assert "def create_ProhibitedType" not in entities
    assert "def create_ActionBase" not in entities
    assert "def create_ExternalMetric" in entities


def test_ontomock_runtime_augmentation_uses_compiled_ownership_without_foreign_terms(
    tmp_path: Path,
) -> None:
    context = build_domain_generation_context(
        domain_config_path=DOMAIN_CONFIG,
        output_root=tmp_path,
        repository_root=ROOT,
        write_files=True,
        planner=_planner,
    )
    iteration = context.iteration_blueprint["iterations"][2]
    classes, properties = _compiled_iteration_owned_surface(iteration)
    prompt = _augment_kg_prompt_with_runtime_rules(
        kg_prompt="Build.",
        entity_label="run",
        entity_uri="https://example.test/run",
        doi_hash="case",
        main_entity_policy={},
        hints_content="SEMANTIC_HINTS_V1\nmetric evidence",
        ontology_contract=context.contract["ontology_publish_contract"],
        compiled_iteration_spec=iteration,
    )

    assert classes == set()
    assert properties == {"hasMetric"}
    assert "Compiled iteration-owned object properties: [hasMetric]" in prompt
    assert "ExternalMetric" in prompt
    for foreign_local in (
        "ChemicalInput",
        "ChemicalSynthesis",
        "hasAddedChemicalInput",
    ):
        assert foreign_local not in prompt
