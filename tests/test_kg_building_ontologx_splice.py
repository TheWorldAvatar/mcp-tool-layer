from __future__ import annotations

import sys
from pathlib import Path

OX = Path(__file__).resolve().parents[1] / "src" / "kg_building" / "ontologx"
if str(OX) not in sys.path:
    sys.path.insert(0, str(OX))

from extraction_hints import _hint_prefix_score, distill_extension_hint
from generate_ontomops_shacl import generate as generate_om
from generate_ontospecies_shacl import generate
from graph_types import GraphDocument, Node, Relationship
from paths import REPO_ROOT
from splice import peel_product_step_types, splice_extension_layer
from strict_noprompt import build_strict_noprompt_extension_prompt


def _graph(*nodes, rels=()):
    by_id = {node.id: node for node in nodes}
    relationships = [
        Relationship(source=by_id[src], target=by_id[tgt], type=typ) for src, typ, tgt in rels
    ]
    return GraphDocument(nodes=list(nodes), relationships=relationships, source=None)


def test_species_shacl_covers_value_nodes_and_forbids_ontosyn_literals():
    text = generate()
    assert "UniqueSpeciesOutputShape" in text
    assert "NoOntosynFormulaLiteralShape" in text
    assert "FormulaNodeMustHaveValueShape" in text
    assert "CCDCNumberNodeMustHaveValueShape" in text
    assert "hasSynthesisStep" not in text


def test_mop_shacl_covers_unique_mop_and_cbu_formula():
    text = generate_om()
    assert "MopRepresentationRequiredShape" in text
    assert "UniqueMopPerOutputShape" in text
    assert "CbuFormulaRequiredShape" in text


def test_collapse_duplicate_species_and_lift_formula_literal():
    graph = _graph(
        Node(id="cs", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "UMC-1"}),
        Node(
            id="3PYo8J",
            type="ontosyn:ChemicalOutput",
            extra_types=["ontospecies:Species"],
            properties={"rdfs:label": "UMC-1", "ontosyn:hasChemicalFormula": "C72H60"},
        ),
        Node(
            id="https_www_theworldavatar_com_kg_instance_ontologx_0c57bac8_3PYo8J",
            type="ontospecies:Species",
            properties={"rdfs:label": "UMC-1"},
        ),
        rels=(
            ("cs", "ontosyn:hasChemicalOutput", "3PYo8J"),
            (
                "cs",
                "ontosyn:hasChemicalOutput",
                "https_www_theworldavatar_com_kg_instance_ontologx_0c57bac8_3PYo8J",
            ),
        ),
    )
    fixed = splice_extension_layer(graph, "ontospecies")
    species = [node for node in fixed.nodes if "ontospecies:Species" in {node.type, *(node.extra_types or [])}]
    assert len(species) == 1
    assert species[0].id == "3PYo8J"
    assert "ontosyn:hasChemicalFormula" not in (species[0].properties or {})
    assert any(node.type == "ontospecies:ChemicalFormula" for node in fixed.nodes)
    outputs = [rel for rel in fixed.relationships if rel.type == "ontosyn:hasChemicalOutput"]
    assert len(outputs) == 1


def test_collapse_duplicate_mops_keeps_seed():
    graph = _graph(
        Node(id="out", type="ontosyn:ChemicalOutput", properties={"rdfs:label": "UMC-1"}),
        Node(id="qaOu0t", type="ontomops:MetalOrganicPolyhedron", properties={"rdfs:label": "seed"}),
        Node(
            id="MetalOrganicPolyhedron-out",
            type="ontomops:MetalOrganicPolyhedron",
            properties={
                "rdfs:label": "UMC-1",
                "ontomops:hasMOPFormula": "{[Zr]2(SDB)3}",
                "ontomops:hasCCDCNumber": "1576897",
            },
        ),
        Node(id="cbu", type="ontomops:ChemicalBuildingUnit", properties={"ontomops:hasCBUFormula": "[SDB]"}),
        rels=(
            ("out", "ontosyn:isRepresentedBy", "qaOu0t"),
            ("out", "ontosyn:isRepresentedBy", "MetalOrganicPolyhedron-out"),
            ("MetalOrganicPolyhedron-out", "ontomops:hasChemicalBuildingUnit", "cbu"),
        ),
    )
    fixed = splice_extension_layer(graph, "ontomops")
    mops = [node for node in fixed.nodes if node.type == "ontomops:MetalOrganicPolyhedron"]
    assert len(mops) == 1
    assert mops[0].id == "qaOu0t"
    assert (mops[0].properties or {}).get("ontomops:hasMOPFormula") == "{[Zr]2(SDB)3}"
    assert any(
        rel.source.id == "qaOu0t" and rel.type == "ontomops:hasChemicalBuildingUnit"
        for rel in fixed.relationships
    )


def test_collapse_unlinked_minted_mop_onto_seed():
    graph = _graph(
        Node(id="out", type="ontosyn:ChemicalOutput", properties={"rdfs:label": "UMC-1"}),
        Node(id="qaOu0tCAQx-BiYqA", type="ontomops:MetalOrganicPolyhedron", properties={"rdfs:label": "seed"}),
        Node(
            id="https_www_theworldavatar_com_kg_instance_ontologx_0c57bac8_qaOu0tCAQx-BiYqA",
            type="ontomops:MetalOrganicPolyhedron",
            properties={"ontomops:hasMOPFormula": "{[Zr]2(SDB)3}", "ontomops:hasCCDCNumber": "1576897"},
        ),
        Node(id="cbu", type="ontomops:ChemicalBuildingUnit", properties={"ontomops:hasCBUFormula": "[SDB]"}),
        rels=(
            ("out", "ontosyn:isRepresentedBy", "qaOu0tCAQx-BiYqA"),
            (
                "https_www_theworldavatar_com_kg_instance_ontologx_0c57bac8_qaOu0tCAQx-BiYqA",
                "ontomops:hasChemicalBuildingUnit",
                "cbu",
            ),
        ),
    )
    fixed = splice_extension_layer(graph, "ontomops")
    mops = [node for node in fixed.nodes if node.type == "ontomops:MetalOrganicPolyhedron"]
    assert [node.id for node in mops] == ["qaOu0tCAQx-BiYqA"]
    assert (mops[0].properties or {}).get("ontomops:hasCCDCNumber") == "1576897"
    assert any(
        rel.source.id == "qaOu0tCAQx-BiYqA" and rel.type == "ontomops:hasChemicalBuildingUnit"
        for rel in fixed.relationships
    )


def test_repo_root_is_v2_clone():
    assert (REPO_ROOT / "src" / "kg_building").is_dir()
    assert (REPO_ROOT / "data" / "ontologies" / "ontospecies-subgraph.ttl").is_file()


def test_hint_prefix_accepts_short_exact_label():
    assert _hint_prefix_score("UMC-1", "UMC-1") > 0
    assert _hint_prefix_score("UMC-1--ca2a81fb94a6", "UMC-1") > 0
    assert _hint_prefix_score("NDBDC", "ADBDC") < 0


def test_distill_extension_hint_is_semantic_hints_not_tbox_recipe():
    raw = (
        "Call create_species then init_memory.\n"
        "```json\n"
        '{"entities": [{"class": "Species", "label": "UMC-1"}]}\n'
        "```\n"
    )
    text = distill_extension_hint(raw, "ontospecies")
    assert text.startswith("SEMANTIC_HINTS_V1")
    assert "create_species" not in text
    assert "init_memory" not in text
    assert '"label": "UMC-1"' in text


def test_peel_product_step_types_keeps_output_and_drops_step_edge():
    graph = _graph(
        Node(id="cs", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "UMC-1"}),
        Node(
            id="UMC1",
            type="ontosyn:ChemicalOutput",
            extra_types=[
                "ontosyn:HeatChill",
                "ontosyn:Sonicate",
                "ontosyn:SynthesisStep",
                "ontomops:MetalOrganicPolyhedron",
                "ontospecies:Species",
            ],
            properties={
                "rdfs:label": ["HeatChill UMC-1", "Sonicate UMC-1", "UMC-1"],
                "ontosyn:hasOrder": 6,
                "ontomops:hasCCDCNumber": "1576897",
            },
        ),
        Node(id="add1", type="ontosyn:Add", extra_types=["ontosyn:SynthesisStep"]),
        rels=(
            ("cs", "ontosyn:hasChemicalOutput", "UMC1"),
            ("cs", "ontosyn:hasSynthesisStep", "UMC1"),
            ("cs", "ontosyn:hasSynthesisStep", "add1"),
        ),
    )
    fixed = peel_product_step_types(graph)
    product = next(node for node in fixed.nodes if node.id == "UMC1")
    types = {product.type, *(product.extra_types or [])}
    assert "ontosyn:ChemicalOutput" in types
    assert "ontomops:MetalOrganicPolyhedron" in types
    assert "ontospecies:Species" in types
    assert "ontosyn:SynthesisStep" not in types
    assert "ontosyn:HeatChill" not in types
    assert "ontosyn:hasOrder" not in (product.properties or {})
    assert (product.properties or {}).get("rdfs:label") == ["UMC-1"]
    step_targets = [
        rel.target.id
        for rel in fixed.relationships
        if rel.type == "ontosyn:hasSynthesisStep"
    ]
    assert step_targets == ["add1"]


def test_extension_strict_prompt_is_generic_plus_surface():
    text = build_strict_noprompt_extension_prompt("ontospecies")
    assert "Generic OntoLogX graph rules" in text
    assert "Occurrence protocol" in text
    assert "EXTENSION_CONTRACT" not in text
    assert "create_species" not in text
    assert "TBox handbook" not in text


def test_extension_hint_path_reads_pipeline_mcp_run(tmp_path):
    from extraction_hints import HINT_RUNS, extension_hint_path, set_hint_runs

    mcp = tmp_path / "runtime" / "abcd1234" / "mcp_run"
    mcp.mkdir(parents=True)
    target = mcp / "ontomops_iter1_hints_UMC-1.txt"
    target.write_text("ledger\n", encoding="utf-8")
    (mcp / "iter3_hints_UMC-1.txt").write_text("main\n", encoding="utf-8")
    previous = list(HINT_RUNS)
    set_hint_runs([str(tmp_path)])
    try:
        found = extension_hint_path("abcd1234", "UMC-1", "ontomops")
    finally:
        set_hint_runs(previous)
    assert found == target
