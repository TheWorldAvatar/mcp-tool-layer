from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.scripts_and_prompts_generation.enrichment_target_sparql import (
    compile_enrichment_target_sparql,
    parse_tbox_paths,
    property_iri_lookup,
    resolve_declared_path_iris,
    validate_enrichment_target_declaration,
)
from src.agents.scripts_and_prompts_generation.domain_generation_config import (
    load_domain_generation_config,
)
from src.pipelines.extensions_kg_building.build import resolve_enrichment_targets


ROOT = Path(__file__).resolve().parents[1]
COMPILER = (
    ROOT
    / "src"
    / "agents"
    / "scripts_and_prompts_generation"
    / "enrichment_target_sparql.py"
)
HAS_OUTPUT = "https://www.theworldavatar.com/kg/OntoSyn/hasChemicalOutput"
REPRESENTED_BY = "https://www.theworldavatar.com/kg/OntoSyn/isRepresentedBy"
MOP_CLASS = "https://www.theworldavatar.com/kg/ontomops/MetalOrganicPolyhedron"
SPECIES_CLASS = (
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#Species"
)
SNAPSHOTS = {
    "ontomops": {
        "path_iris": [HAS_OUTPUT, REPRESENTED_BY],
        "target_class_iri": MOP_CLASS,
        "file": ROOT / "configs" / "sparql" / "extensions" / "ontomops_enrichment_target.sparql",
    },
    "ontospecies": {
        "path_iris": [HAS_OUTPUT],
        "target_class_iri": SPECIES_CLASS,
        "file": ROOT
        / "configs"
        / "sparql"
        / "extensions"
        / "ontospecies_enrichment_target.sparql",
    },
}


def test_compiler_source_has_no_domain_literals() -> None:
    source = COMPILER.read_text(encoding="utf-8")
    for token in (
        "hasChemicalOutput",
        "isRepresentedBy",
        "MetalOrganicPolyhedron",
        "OntoSyn",
        "ontospecies",
        "ontomops",
    ):
        assert token not in source


def test_declaration_rejects_handwritten_sparql() -> None:
    with pytest.raises(ValueError, match="non-allowlisted"):
        validate_enrichment_target_declaration(
            {
                "path": ["hasThing"],
                "query_file": "configs/sparql/extensions/hand.sparql",
            },
            prefix="runtime.enrichment_target",
        )
    with pytest.raises(ValueError, match="non-allowlisted"):
        validate_enrichment_target_declaration(
            {"path": ["hasThing"], "query": "SELECT ?target WHERE { ?s ?p ?o }"},
            prefix="runtime.enrichment_target",
        )


def test_declared_hops_resolve_from_supporting_tbox() -> None:
    parsed = parse_tbox_paths(
        [
            ROOT / "data" / "ontologies" / "ontomops-subgraph.ttl",
            ROOT / "data" / "ontologies" / "ontosynthesis.ttl",
        ]
    )
    lookup = property_iri_lookup(parsed)
    assert resolve_declared_path_iris(
        ["hasChemicalOutput", "isRepresentedBy"],
        lookup=lookup,
    ) == [HAS_OUTPUT, REPRESENTED_BY]


@pytest.mark.parametrize("ontology_name", sorted(SNAPSHOTS))
def test_repo_snapshots_are_compiler_output(ontology_name: str) -> None:
    spec = SNAPSHOTS[ontology_name]
    compiled = compile_enrichment_target_sparql(
        path_iris=spec["path_iris"],
        target_class_iri=spec["target_class_iri"],
    )
    assert spec["file"].read_text(encoding="utf-8") == compiled


@pytest.mark.parametrize("ontology_name", ["ontomops", "ontospecies"])
def test_extension_domain_configs_declare_path_only(ontology_name: str) -> None:
    config = load_domain_generation_config(
        ROOT / "configs" / "domains" / f"{ontology_name}.json",
        repository_root=ROOT,
    )
    declared = config.runtime["enrichment_target"]
    assert "query_file" not in declared
    assert "query" not in declared
    assert declared["path"]


def test_compiler_query_matches_legacy_resolution_on_fixture_graph() -> None:
    ttl = """
        @prefix syn: <https://www.theworldavatar.com/kg/OntoSyn/> .
        @prefix os: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#> .
        @prefix mop: <https://www.theworldavatar.com/kg/ontomops/> .
        <https://example.test/synthesis/1> syn:hasChemicalOutput <https://example.test/output/1> .
        <https://example.test/output/1> a syn:ChemicalOutput, os:Species ;
            syn:isRepresentedBy <https://example.test/mop/1> .
        <https://example.test/mop/1> a mop:MetalOrganicPolyhedron .
    """
    for ontology_name, class_iri, expected in (
        ("ontospecies", SPECIES_CLASS, "https://example.test/output/1"),
        ("ontomops", MOP_CLASS, "https://example.test/mop/1"),
    ):
        targets = resolve_enrichment_targets(
            ontology_name=ontology_name,
            entity_uri="https://example.test/synthesis/1",
            main_ontology_ttl=ttl,
            meta_cfg={
                "ontologies": {
                    "extensions": [
                        {
                            "name": ontology_name,
                            "runtime_policies": {
                                "enrichment_target": {
                                    "query_file": (
                                        f"configs/sparql/extensions/"
                                        f"{ontology_name}_enrichment_target.sparql"
                                    ),
                                    "target_variable": "target",
                                    "target_class_iri": class_iri,
                                    "cardinality": "exactly_one",
                                }
                            },
                        }
                    ]
                }
            },
            project_root=str(ROOT),
        )
        assert targets[0]["target_iri"] == expected


def test_load_rejects_handwritten_sparql_on_domain_config(tmp_path: Path) -> None:
    raw = json.loads(
        (ROOT / "configs" / "domains" / "ontomops.json").read_text(encoding="utf-8")
    )
    raw["runtime"]["enrichment_target"]["query_file"] = "hand.sparql"
    path = tmp_path / "ontomops.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="non-allowlisted"):
        load_domain_generation_config(path, repository_root=ROOT)
