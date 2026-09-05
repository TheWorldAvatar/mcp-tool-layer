from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "baselines" / "ontologx_ontosyn"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

from extraction_hints import (
    _hint_prefix_score,
    bound_chemical_outputs,
    distill_ontospecies_hint,
    extension_hint_path,
)
from generate_ontomops_shacl import generate as generate_om
from generate_ontospecies_shacl import generate
from graph_merge import (
    attach_subgraph,
    reattach_detached_species_facts,
    seed_mop_targets,
    seed_species_outputs,
)
from graph_types import GraphDocument, Node, Relationship
from ontology_graph import load_ontology_graph
from prompt_builder import EXTENSION_CONTRACT, main_ttl_extension_suffix
from ttl_export import graph_to_rdflib, graph_to_turtle, read_ttl, write_ttl


def _graph(*nodes, rels=()):
    by_id = {node.id: node for node in nodes}
    relationships = [
        Relationship(source=by_id[src], target=by_id[tgt], type=typ) for src, typ, tgt in rels
    ]
    return GraphDocument(nodes=list(nodes), relationships=relationships, source=None)


def test_ontospecies_shacl_has_species_output_contract():
    text = generate()
    assert "ontospecies:SpeciesShape" in text
    assert "hasMolecularFormulaValue" in text
    assert "hasCCDCNumberValue" in text
    assert "SpeciesOutputRequiredShape" in text
    assert "UniqueSpeciesOutputShape" in text
    assert "SpeciesMustBeOutputShape" in text
    assert "hasSynthesisStep" not in text


def test_ontospecies_ontology_model_has_bridge_edge():
    ontology = load_ontology_graph(REPO_ROOT / "data" / "ontologies" / "ontospecies-subgraph.ttl")
    types = {node.type for node in ontology.nodes}
    assert "ontospecies:Species" in types
    assert "ontosyn:ChemicalSynthesis" in types
    assert "ontospecies:MolecularFormula" in types
    triples = {(rel.source.type, rel.type, rel.target.type) for rel in ontology.relationships}
    assert ("ontosyn:ChemicalSynthesis", "ontosyn:hasChemicalOutput", "ontospecies:Species") in triples
    assert ("ontospecies:Species", "ontospecies:hasMolecularFormula", "ontospecies:MolecularFormula") in triples


def test_attach_adds_species_as_extra_type_on_chemical_output():
    main = _graph(
        Node(id="cs", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "S"}),
        Node(id="out", type="ontosyn:ChemicalOutput", properties={"rdfs:label": "MOP-1"}),
        rels=(("cs", "ontosyn:hasChemicalOutput", "out"),),
    )
    delta = _graph(
        Node(id="out", type="ontospecies:Species", properties={"ontospecies:hasProductName": "MOP-1"}),
        Node(
            id="mf",
            type="ontospecies:MolecularFormula",
            properties={"ontospecies:hasMolecularFormulaValue": "C12H8O4"},
        ),
        rels=(("out", "ontospecies:hasMolecularFormula", "mf"),),
    )
    merged = attach_subgraph(main, delta, reuse="layer")
    out = next(node for node in merged.nodes if node.id == "out")
    assert out.type == "ontosyn:ChemicalOutput"
    assert "ontospecies:Species" in out.extra_types
    rdf = graph_to_rdflib(merged, "abcd1234")
    iris = {str(obj) for _, pred, obj in rdf if str(pred).endswith("type") and str(_).endswith("/out")}
    assert "https://www.theworldavatar.com/kg/OntoSyn/ChemicalOutput" in iris
    assert "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#Species" in iris
    assert bound_chemical_outputs(merged)[0]["output_id"] == "out"


def test_ttl_roundtrip_keeps_extra_type(tmp_path):
    graph = _graph(
        Node(
            id="out",
            type="ontosyn:ChemicalOutput",
            properties={"rdfs:label": "MOP-1"},
            extra_types=["ontospecies:Species"],
        )
    )
    path = write_ttl(graph, "abcd1234", tmp_path / "out.ttl")
    loaded = read_ttl(path, "abcd1234")
    node = loaded.nodes[0]
    assert node.id == "out"
    assert node.type == "ontosyn:ChemicalOutput"
    assert "ontospecies:Species" in node.extra_types


def test_distill_keeps_json_drops_mcp():
    prompt = (
        "Use create_Species and init_memory.\n"
        "Here are the extraction iteration hints (ref-entity-relations.v1 JSON):\n\n"
        "```json\n"
        '{"entities":[{"ref":"species1","class":"Species","label":"MOP-1"}],"relations":[]}\n'
        "```\n"
        "Then call export_memory.\n"
    )
    distilled = distill_ontospecies_hint(prompt)
    assert "Species" in distilled
    assert "MOP-1" in distilled
    assert "init_memory" not in distilled
    assert "create_Species" not in distilled


def test_extension_prompt_pastes_main_ttl_not_inventory():
    graph = _graph(
        Node(id="cs", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "S"}),
        Node(id="out", type="ontosyn:ChemicalOutput", properties={"rdfs:label": "MOP-1"}),
        rels=(("cs", "ontosyn:hasChemicalOutput", "out"),),
    )
    turtle = graph_to_turtle(graph, "abcd1234")
    suffix = main_ttl_extension_suffix(
        turtle,
        enrichment_targets=[
            {
                "name": "primary",
                "target_iri": "https://www.theworldavatar.com/kg/instance/ontologx/abcd1234/out",
                "class_iri": "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#Species",
                "source": "configs/sparql/extensions/ontospecies_enrichment_target.sparql",
            }
        ],
    )
    assert "Here is the canonical main-ontology TTL for this upstream entity:" in suffix
    assert "ontosyn:ChemicalSynthesis" in suffix
    assert "ChemicalOutput" in suffix or "out" in suffix
    assert "EXISTING_GRAPH_INVENTORY" not in suffix
    assert "EXISTING_GRAPH_INVENTORY" not in EXTENSION_CONTRACT
    assert "target_iri" in suffix


def test_hint_prefix_prefers_ndbdc_over_adbdc():
    ndb = "Synthesis of (TMA)4{[V6O6(OCH3)9(PhPO3)]2(NDBDC)3}-6CH3OH-3DMF (TMA-VMOC-P-2)"
    adb = "Synthesis of (TMA)4{[V6O6(OCH3)9(PhPO3)]2(ADBDC)3}-3CH3OH-2DMF (TMA-VMOC-P-3)"
    ndb_stem = "Synthesis_of_TMA_4_V6O6_OCH3_9_PhPO3_2_NDB--11504502e2c5"
    adb_stem = "Synthesis_of_TMA_4_V6O6_OCH3_9_PhPO3_2_ADB--5cfe34cabf6e"
    assert _hint_prefix_score(ndb_stem, ndb) > _hint_prefix_score(adb_stem, ndb)
    assert _hint_prefix_score(adb_stem, adb) > _hint_prefix_score(ndb_stem, adb)


def test_extension_hint_path_unique_match(tmp_path, monkeypatch):
    run_root = tmp_path / "scenarios" / "mops" / "runs" / "hintrun" / "runtime" / "1b9180ec"
    (run_root / "mcp_run").mkdir(parents=True)
    (run_root / "mcp_run" / "iter3_hints_dummy.txt").write_text("x", encoding="utf-8")
    prompt_dir = run_root / "prompts" / "ontospecies_kg_building"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "Synthesis_of_TMA_4_V6O6_OCH3_9_PhPO3_2_NDB--11504502e2c5.md").write_text(
        "ndb", encoding="utf-8"
    )
    (prompt_dir / "Synthesis_of_TMA_4_V6O6_OCH3_9_PhPO3_2_ADB--5cfe34cabf6e.md").write_text(
        "adb", encoding="utf-8"
    )
    monkeypatch.setattr("extraction_hints.REPO_ROOT", tmp_path)
    monkeypatch.setattr("extraction_hints.HINT_RUNS", ["hintrun"])
    path = extension_hint_path(
        "1b9180ec",
        "Synthesis of (TMA)4{[V6O6(OCH3)9(PhPO3)]2(NDBDC)3}-6CH3OH-3DMF (TMA-VMOC-P-2)",
        "ontospecies",
    )
    assert path is not None
    assert "NDB" in path.name


def test_reattach_matches_short_product_code():
    main = _graph(
        Node(
            id="out",
            type="ontosyn:ChemicalOutput",
            properties={"rdfs:label": "ZrT-2"},
            extra_types=["ontospecies:Species"],
        ),
        Node(id="cs", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "ZrT-2"}),
        Node(
            id="species-zrt2",
            type="ontospecies:Species",
            properties={"rdfs:label": "{[Cp3Zr3]4(BTC)4}4+ (ZrT-2)"},
        ),
        Node(id="ea", type="ontospecies:ElementalAnalysisData", properties={}),
        rels=(
            ("cs", "ontosyn:hasChemicalOutput", "out"),
            ("species-zrt2", "ontospecies:hasElementalAnalysisData", "ea"),
        ),
    )
    fixed = reattach_detached_species_facts(main)
    rels = {(rel.source.id, rel.type, rel.target.id) for rel in fixed.relationships}
    assert ("out", "ontospecies:hasElementalAnalysisData", "ea") in rels


def test_reattach_moves_detached_species_facts_onto_output():
    main = _graph(
        Node(
            id="out",
            type="ontosyn:ChemicalOutput",
            properties={"rdfs:label": "VMOP-16"},
            extra_types=["ontospecies:Species"],
        ),
        Node(id="cs", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "VMOP-16"}),
        Node(id="species_vmop16", type="ontospecies:Species", properties={"rdfs:label": "VMOP-16"}),
        Node(id="ir", type="ontospecies:InfraredSpectroscopyData", properties={}),
        rels=(
            ("cs", "ontosyn:hasChemicalOutput", "out"),
            ("species_vmop16", "ontospecies:hasInfraredSpectroscopyData", "ir"),
        ),
    )
    fixed = reattach_detached_species_facts(main)
    ids = {node.id for node in fixed.nodes}
    assert "out" in ids
    assert "species_vmop16" not in ids
    rels = {(rel.source.id, rel.type, rel.target.id) for rel in fixed.relationships}
    assert ("out", "ontospecies:hasInfraredSpectroscopyData", "ir") in rels


def test_seed_species_adds_extra_type():
    main = _graph(
        Node(id="cs", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "S"}),
        Node(id="out", type="ontosyn:ChemicalOutput", properties={"rdfs:label": "MOP-1"}),
        rels=(("cs", "ontosyn:hasChemicalOutput", "out"),),
    )
    seeded = seed_species_outputs(main)
    out = next(node for node in seeded.nodes if node.id == "out")
    assert out.type == "ontosyn:ChemicalOutput"
    assert "ontospecies:Species" in out.extra_types
    rdf = graph_to_rdflib(seeded, "abcd1234")
    types = {str(obj) for _, pred, obj in rdf if str(pred).endswith("type") and str(_).endswith("/out")}
    assert any(item.endswith("Species") for item in types)


def test_percentage_exports_as_xsd_float():
    graph = _graph(
        Node(
            id="wp",
            type="ontospecies:WeightPercentage",
            properties={"ontospecies:hasPercentageValue": 32.28},
        )
    )
    rdf = graph_to_rdflib(graph, "abcd1234")
    literals = [obj for _, _, obj in rdf if getattr(obj, "datatype", None)]
    assert any(str(item.datatype).endswith("float") for item in literals)


def test_seed_mop_adds_is_represented_by():
    main = _graph(
        Node(id="cs", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "S"}),
        Node(id="out", type="ontosyn:ChemicalOutput", properties={"rdfs:label": "VMOP-16"}),
        rels=(("cs", "ontosyn:hasChemicalOutput", "out"),),
    )
    seeded = seed_mop_targets(main)
    rels = {(rel.source.id, rel.type, rel.target.id) for rel in seeded.relationships}
    assert any(item[1] == "ontosyn:isRepresentedBy" for item in rels)
    assert any(node.type == "ontomops:MetalOrganicPolyhedron" for node in seeded.nodes)


def test_ontomops_shacl_requires_mop_link():
    text = generate_om()
    assert "MopRepresentationRequiredShape" in text
    assert "hasChemicalBuildingUnit" in text
    assert "isRepresentedBy" in text


def test_ontomops_ontology_has_bridge():
    ontology = load_ontology_graph(REPO_ROOT / "data" / "ontologies" / "ontomops-subgraph.ttl")
    types = {node.type for node in ontology.nodes}
    assert "ontomops:MetalOrganicPolyhedron" in types
    assert "ontomops:ChemicalBuildingUnit" in types
    triples = {(rel.source.type, rel.type, rel.target.type) for rel in ontology.relationships}
    assert (
        "ontosyn:ChemicalOutput",
        "ontosyn:isRepresentedBy",
        "ontomops:MetalOrganicPolyhedron",
    ) in triples
