from __future__ import annotations

import json
from pathlib import Path

from rdflib import Graph, Literal, RDF, RDFS, URIRef

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    _base_script,
    _checks_script,
    _entities_script,
    _iteration_kg_prompt,
)
from src.agents.scripts_and_prompts_generation.check_existing_generation_experiment import (
    run_experiment,
)
from src.agents.scripts_and_prompts_generation.reuse_policy import (
    attach_reuse_policy,
    existing_entity_check_contracts,
    prohibited_class_locals,
)
from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import (
    load_central_reuse_memory,
    load_document_reuse_memory,
    publish_reusable_entities_to_central_memory,
    publish_reusable_entities_to_document_memory,
)


ROOT = Path(__file__).resolve().parents[1]
META_CONFIG = ROOT / "configs" / "meta_task" / "meta_task_config.json"
REUSE_POLICY = ROOT / "configs" / "meta_task" / "ontosynthesis_binary_reuse_review.json"


def _context(tmp_path: Path):
    context = build_agentic_generation_context(
        ontology_name="ontosynthesis",
        meta_task_config_path=META_CONFIG,
        output_root=tmp_path,
        write_files=False,
    )
    attach_reuse_policy(context.contract, REUSE_POLICY)
    return context


def test_crystallize_is_prohibited_outside_the_tbox(tmp_path: Path) -> None:
    context = _context(tmp_path)

    assert prohibited_class_locals(context.contract["reuse_policy"]) == {
        "Crystallize"
    }
    assert "integrity_annotations" not in context.parsed["classes"]["Crystallize"]


def test_reuse_policy_exposes_central_and_scoped_checks(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    contracts = existing_entity_check_contracts(
        parsed=context.parsed,
        contract=context.contract,
        legacy_all_classes_when_absent=False,
    )
    tools = {str(item["public_tool"]) for item in contracts}

    assert "check_existing_Supplier" in tools
    assert "check_existing_MetalOrganicPolyhedron" in tools
    assert "check_existing_ChemicalInput" in tools
    by_tool = {str(item["public_tool"]): item for item in contracts}
    assert by_tool["check_existing_Supplier"]["lookup_scope"] == "central"
    assert by_tool["check_existing_Supplier"]["reuse_authorized"] is True
    assert by_tool["check_existing_Document"]["lookup_scope"] == "document"
    assert by_tool["check_existing_Document"]["reuse_authorized"] is True
    assert by_tool["check_existing_ChemicalInput"]["lookup_scope"] == "scoped"
    assert by_tool["check_existing_ChemicalInput"]["reuse_authorized"] is False
    assert (
        by_tool["check_existing_ChemicalInput"]["reference_resolution_only"]
        is True
    )

    source = _checks_script(context)
    assert "datatype_values" in source
    assert "outgoing_relations" in source
    assert "incoming_relations" in source
    assert "central_provenance" in source
    assert "load_central_reuse_memory" in source
    assert "retained_graph" in source
    assert 'if lookup_scope == "central"' in source
    assert "creation_base import GRAPH" not in source
    assert "def check_existing_Supplier(" in source
    assert "def check_existing_ChemicalInput(" in source
    assert "proposed_entity_json: str" in source
    assert "label: str = \"\"" in source
    assert 'json.dumps({"label": label}' in source

    base_source = _base_script(context)
    entity_source = _entities_script(context)
    assert "GRAPH = rdf_runtime.retained_graph()" in base_source
    assert "**metadata" in base_source
    assert "URIRef(_ENTITY_CAPABILITIES" in entity_source


def test_kg_prompt_requires_check_before_create_for_reusable_classes(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    iteration = (context.iteration_blueprint.get("iterations") or [])[0]
    prompt = _iteration_kg_prompt(context, iteration)

    assert (
        "For a reusable class newly proposed by this iteration without an exact "
        "dossier IRI, call its central `check_existing_*`"
        in prompt
    )
    assert "Bind an exact prior IRI from the pipeline identity dossier directly" in prompt
    assert "`Document`: call `check_existing_Document` first" in prompt
    assert "`ChemicalInput`: call `check_existing_ChemicalInput` for exact" in prompt
    assert "lookup_scope=`scoped`; generic reuse forbidden" in prompt
    assert "never use it to merge a newly declared occurrence" in prompt
    assert "`Supplier`: call `check_existing_Supplier` first" not in prompt


def test_three_isolated_check_generations_are_valid_and_deterministic(
    tmp_path: Path,
) -> None:
    summary = run_experiment(
        ontology_name="ontosynthesis",
        meta_task_config_path=META_CONFIG,
        reuse_policy_path=REUSE_POLICY,
        output_dir=tmp_path / "trials",
        trials=3,
    )

    assert summary["valid_trials"] == 3
    assert summary["all_trials_valid"] is True
    assert summary["deterministic_across_trials"] is True
    assert len(summary["script_hashes"]) == 1
    persisted = json.loads(
        (tmp_path / "trials" / "summary.json").read_text(encoding="utf-8")
    )
    assert persisted["authorized_checks"] == summary["authorized_checks"]


def test_central_reuse_memory_is_independent_and_cross_scope(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path / "runtime"))
    ontology = "central-memory-test"
    reusable_class = URIRef("https://example.test/Supplier")
    first = URIRef("https://example.test/supplier/first")
    second = URIRef("https://example.test/supplier/second")

    first_scope = Graph()
    first_scope.add((first, RDF.type, reusable_class))
    first_scope.add((first, RDFS.label, Literal("First Supplier")))
    publish_reusable_entities_to_central_memory(
        ontology_name=ontology,
        source_graph=first_scope,
        reusable_class_iris=[str(reusable_class)],
        doi="document-1",
        top_level_entity_name="top-1",
    )

    second_scope = Graph()
    second_scope.add((second, RDF.type, reusable_class))
    second_scope.add((second, RDFS.label, Literal("Second Supplier")))
    publish_reusable_entities_to_central_memory(
        ontology_name=ontology,
        source_graph=second_scope,
        reusable_class_iris=[str(reusable_class)],
        doi="document-2",
        top_level_entity_name="top-2",
    )

    central, provenance = load_central_reuse_memory(ontology)
    assert set(central.subjects(RDF.type, reusable_class)) == {first, second}
    assert provenance[str(first)] == [
        {"doi": "document-1", "top_level_entity_name": "top-1"}
    ]
    assert provenance[str(second)] == [
        {"doi": "document-2", "top_level_entity_name": "top-2"}
    ]

    first_scope.add((first, RDFS.label, Literal("Unpublished mutation")))
    reloaded, _ = load_central_reuse_memory(ontology)
    assert (first, RDFS.label, Literal("Unpublished mutation")) not in reloaded


def test_document_reuse_memory_is_deterministically_isolated_by_doi(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path / "runtime"))
    ontology = "document-memory-test"
    document_class = URIRef("http://purl.org/ontology/bibo/Document")
    first = URIRef("https://example.test/document/first")
    second = URIRef("https://example.test/document/second")

    for doi, node in (("document-1", first), ("document-2", second)):
        source = Graph()
        source.add((node, RDF.type, document_class))
        source.add((node, RDFS.label, Literal(doi)))
        publish_reusable_entities_to_document_memory(
            ontology_name=ontology,
            source_graph=source,
            reusable_class_iris=[str(document_class)],
            doi=doi,
            top_level_entity_name="top",
        )

    first_graph, first_provenance = load_document_reuse_memory(
        ontology, "document-1"
    )
    second_graph, _ = load_document_reuse_memory(ontology, "document-2")

    assert set(first_graph.subjects(RDF.type, document_class)) == {first}
    assert set(second_graph.subjects(RDF.type, document_class)) == {second}
    assert first_provenance[str(first)] == [
        {"doi": "document-1", "top_level_entity_name": "top"}
    ]
