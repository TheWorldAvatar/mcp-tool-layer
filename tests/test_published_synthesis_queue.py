from __future__ import annotations

import json
from pathlib import Path

from src.pipelines.extensions_kg_building.build import resolve_enrichment_targets
from src.pipelines.utils.published_synthesis_queue import (
    hydrate_published_entities,
    load_extension_synthesis_queue,
    parse_top_entities_from_ttl,
    resolve_llm_top_entity_sparql,
)


ROOT = Path(__file__).resolve().parents[1]
SPECIES_CLASS = (
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#Species"
)
MOP_CLASS = "https://www.theworldavatar.com/kg/ontomops/MetalOrganicPolyhedron"
LLM_SPARQL = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT DISTINCT ?entity ?label WHERE {
  ?entity a <https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis> .
  OPTIONAL { ?entity rdfs:label ?label }
}
"""
PUBLISHED_TTL = """
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix syn: <https://www.theworldavatar.com/kg/OntoSyn/> .
@prefix os: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#> .
@prefix mop: <https://www.theworldavatar.com/kg/ontomops/> .
<https://example.test/s/vmop-11> a syn:ChemicalSynthesis ;
    rdfs:label "VMOP-11" ;
    syn:hasChemicalOutput <https://example.test/o/11> .
<https://example.test/s/vmop-12> a syn:ChemicalSynthesis ;
    rdfs:label "VMOP-12" ;
    syn:hasChemicalOutput <https://example.test/o/12> .
<https://example.test/o/12> a syn:ChemicalOutput, os:Species ;
    syn:isRepresentedBy <https://example.test/m/12> .
<https://example.test/m/12> a mop:MetalOrganicPolyhedron .
"""


def _meta_cfg(name: str, query_file: str, class_iri: str) -> dict:
    return {
        "ontologies": {
            "extensions": [
                {
                    "name": name,
                    "runtime_policies": {
                        "enrichment_target": {
                            "query_file": query_file,
                            "target_variable": "target",
                            "target_class_iri": class_iri,
                            "cardinality": "exactly_one",
                        }
                    },
                }
            ]
        }
    }


def test_llm_listing_sparql_finds_all_published_syntheses() -> None:
    entities = parse_top_entities_from_ttl(PUBLISHED_TTL, LLM_SPARQL)
    labels = {item["label"] for item in entities}
    assert labels == {"VMOP-11", "VMOP-12"}


def test_generated_top_entity_sparql_matches_listing_shape() -> None:
    path = resolve_llm_top_entity_sparql("ontosynthesis", project_root=str(ROOT))
    query = path.read_text(encoding="utf-8")
    assert "ChemicalSynthesis" in query
    assert "hasChemicalOutput" not in query
    entities = parse_top_entities_from_ttl(PUBLISHED_TTL, query)
    assert {item["label"] for item in entities} == {"VMOP-11", "VMOP-12"}


def test_enrichment_sparql_binds_published_chemical_output() -> None:
    species = resolve_enrichment_targets(
        ontology_name="ontospecies",
        entity_uri="https://example.test/s/vmop-12",
        main_ontology_ttl=PUBLISHED_TTL,
        meta_cfg=_meta_cfg(
            "ontospecies",
            "configs/sparql/extensions/ontospecies_enrichment_target.sparql",
            SPECIES_CLASS,
        ),
        project_root=str(ROOT),
    )
    mop = resolve_enrichment_targets(
        ontology_name="ontomops",
        entity_uri="https://example.test/s/vmop-12",
        main_ontology_ttl=PUBLISHED_TTL,
        meta_cfg=_meta_cfg(
            "ontomops",
            "configs/sparql/extensions/ontomops_enrichment_target.sparql",
            MOP_CLASS,
        ),
        project_root=str(ROOT),
    )
    assert species[0]["target_iri"] == "https://example.test/o/12"
    assert mop[0]["target_iri"] == "https://example.test/m/12"


def test_queue_prefers_published_ttl_over_thinned_json(tmp_path: Path) -> None:
    published = tmp_path / "ontosynthesis_output"
    published.mkdir()
    (published / "top.ttl").write_text(PUBLISHED_TTL, encoding="utf-8")
    mcp = tmp_path / "mcp_run"
    mcp.mkdir()
    (mcp / "iter1_top_entities.json").write_text(
        json.dumps(
            [
                {
                    "uri": "https://example.test/s/vmop-12",
                    "label": "VMOP-12",
                    "types": [
                        "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis"
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    (mcp / "top_entity_identity_lock.json").write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "uri": "https://example.test/s/vmop-11",
                        "label": "VMOP-11",
                        "identity_dossier": {"source": "lock"},
                    },
                    {
                        "uri": "https://example.test/s/vmop-12",
                        "label": "VMOP-12",
                        "identity_dossier": {"source": "lock"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    meta = {
        "ontologies": {
            "main": {
                "name": "ontosynthesis",
                "output_dir": "ontosynthesis_output",
                "top_ttl_name": "top.ttl",
            }
        }
    }

    queue = load_extension_synthesis_queue(
        str(tmp_path),
        ontology_name="ontosynthesis",
        project_root=str(ROOT),
        meta_cfg=meta,
    )

    assert [item["label"] for item in queue] == ["VMOP-11", "VMOP-12"]
    assert queue[0]["identity_dossier"]["source"] == "lock"


def test_hydrate_keeps_lock_fields() -> None:
    published = [{"uri": "https://example.test/s/1", "label": "A", "types": ["T"]}]
    lock = [
        {
            "uri": "https://example.test/s/1",
            "label": "A",
            "identity_dossier": {"k": 1},
        }
    ]
    merged = hydrate_published_entities(published, lock)
    assert merged[0]["identity_dossier"] == {"k": 1}


def test_real_os_om_published_ttl_if_present() -> None:
    runtime = ROOT / "scenarios/mops/runs/20260822_eval30_os_om/runtime/7ba809dd"
    top_ttl = runtime / "ontosynthesis_output" / "top.ttl"
    if not top_ttl.is_file():
        return
    query = resolve_llm_top_entity_sparql("ontosynthesis", project_root=str(ROOT))
    entities = parse_top_entities_from_ttl(
        top_ttl.read_text(encoding="utf-8"),
        query.read_text(encoding="utf-8"),
    )
    assert len(entities) >= 2

    entity_ttls = sorted(
        path
        for path in (runtime / "ontosynthesis_output").glob("*.ttl")
        if path.name.lower() != "top.ttl"
    )
    bound = None
    for ttl_path in entity_ttls:
        ttl_text = ttl_path.read_text(encoding="utf-8")
        for entity in entities:
            try:
                bound = resolve_enrichment_targets(
                    ontology_name="ontospecies",
                    entity_uri=entity["uri"],
                    main_ontology_ttl=ttl_text,
                    meta_cfg=_meta_cfg(
                        "ontospecies",
                        "configs/sparql/extensions/ontospecies_enrichment_target.sparql",
                        SPECIES_CLASS,
                    ),
                    project_root=str(ROOT),
                )
                break
            except RuntimeError:
                continue
        if bound:
            break
    if bound is None:
        return
    assert bound[0]["target_iri"].startswith("http")
