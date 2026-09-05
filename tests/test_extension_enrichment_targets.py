from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from rdflib import RDF, URIRef

from src.pipelines.extensions_kg_building.build import resolve_enrichment_targets


ROOT = Path(__file__).resolve().parents[1]
SYNTHESIS = "https://example.test/synthesis/1"
OUTPUT = "https://example.test/output/1"
MOP = "https://example.test/mop/1"
SPECIES_CLASS = (
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#Species"
)
MOP_CLASS = "https://www.theworldavatar.com/kg/ontomops/MetalOrganicPolyhedron"


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


@pytest.mark.parametrize(
    ("ontology_name", "query_file", "class_iri", "expected"),
    [
        (
            "ontospecies",
            "configs/sparql/extensions/ontospecies_enrichment_target.sparql",
            SPECIES_CLASS,
            OUTPUT,
        ),
        (
            "ontomops",
            "configs/sparql/extensions/ontomops_enrichment_target.sparql",
            MOP_CLASS,
            MOP,
        ),
    ],
)
def test_configured_sparql_resolves_exact_enrichment_identity(
    ontology_name: str,
    query_file: str,
    class_iri: str,
    expected: str,
) -> None:
    ttl = f"""
        @prefix syn: <https://www.theworldavatar.com/kg/OntoSyn/> .
        @prefix os: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#> .
        @prefix mop: <https://www.theworldavatar.com/kg/ontomops/> .
        <{SYNTHESIS}> syn:hasChemicalOutput <{OUTPUT}> .
        <{OUTPUT}> a syn:ChemicalOutput, os:Species ;
            syn:isRepresentedBy <{MOP}> .
        <{MOP}> a mop:MetalOrganicPolyhedron .
    """

    targets = resolve_enrichment_targets(
        ontology_name=ontology_name,
        entity_uri=SYNTHESIS,
        main_ontology_ttl=ttl,
        meta_cfg=_meta_cfg(ontology_name, query_file, class_iri),
        project_root=str(ROOT),
    )

    assert targets == [
        {
            "name": "primary",
            "target_iri": expected,
            "class_iri": class_iri,
            "source": query_file,
        }
    ]


def test_enrichment_target_resolution_fails_closed_on_split_identity() -> None:
    ttl = f"""
        @prefix syn: <https://www.theworldavatar.com/kg/OntoSyn/> .
        @prefix os: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#> .
        <{SYNTHESIS}> syn:hasChemicalOutput <{OUTPUT}>, <urn:other> .
        <{OUTPUT}> a os:Species .
        <urn:other> a os:Species .
    """

    with pytest.raises(RuntimeError, match="exactly one URI"):
        resolve_enrichment_targets(
            ontology_name="ontospecies",
            entity_uri=SYNTHESIS,
            main_ontology_ttl=ttl,
            meta_cfg=_meta_cfg(
                "ontospecies",
                "configs/sparql/extensions/ontospecies_enrichment_target.sparql",
                SPECIES_CLASS,
            ),
            project_root=str(ROOT),
        )


@pytest.mark.parametrize(
    ("ontology_name", "class_iri", "target_iri", "runtime_module"),
    [
        (
            "ontospecies",
            SPECIES_CLASS,
            OUTPUT,
            "ai_generated_contents_candidate.scripts.ontospecies._fixed_rdf_runtime",
        ),
        (
            "ontomops",
            MOP_CLASS,
            MOP,
            "ai_generated_contents_candidate.scripts.ontomops._fixed_rdf_runtime",
        ),
    ],
)
def test_generated_creator_reuses_sparql_bound_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ontology_name: str,
    class_iri: str,
    target_iri: str,
    runtime_module: str,
) -> None:
    module = __import__(runtime_module, fromlist=["dummy"])
    state = {
        "enrichment_targets": [
            {"target_iri": target_iri, "class_iri": class_iri}
        ]
    }
    (tmp_path / f"{ontology_name}_global_state.json").write_text(
        json.dumps(state),
        encoding="utf-8",
    )
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    module.reset_retained_graph()

    created = module.package_entity_capabilities()[class_iri]("canonical target")

    assert created == target_iri


def _load_generated_runtime(path: Path):
    spec = importlib.util.spec_from_file_location(
        f"generated_runtime_{path.parent.name}", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("ontology_name", "class_iri", "target_iri", "runtime_path"),
    [
        (
            "ontospecies",
            SPECIES_CLASS,
            OUTPUT,
            ROOT
            / "ai_generated_contents_ontosyn_extensions_regen_v6"
            / "scripts"
            / "ontospecies"
            / "_fixed_rdf_runtime.py",
        ),
        (
            "ontomops",
            MOP_CLASS,
            MOP,
            ROOT
            / "ai_generated_contents_ontosyn_extensions_regen_v6"
            / "scripts"
            / "ontomops"
            / "_fixed_rdf_runtime.py",
        ),
    ],
)
def test_generated_init_memory_seeds_bound_enrichment_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ontology_name: str,
    class_iri: str,
    target_iri: str,
    runtime_path: Path,
) -> None:
    module = _load_generated_runtime(runtime_path)
    (tmp_path / f"{ontology_name}_global_state.json").write_text(
        json.dumps(
            {"enrichment_targets": [{"target_iri": target_iri, "class_iri": class_iri}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path))
    module.reset_retained_graph()

    initialized = json.loads(module.init_memory("case-hash", "top"))

    seed = initialized["enrichment_target_seed"]
    assert seed["applied"] is True
    assert seed["seeded"] == [target_iri]
    assert (
        URIRef(target_iri),
        RDF.type,
        URIRef(class_iri),
    ) in module.retained_graph()
