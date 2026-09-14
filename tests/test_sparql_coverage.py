from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from src.kg_building.abox_health.sparql_coverage import (
    collect_sparql_coverage,
    f1_from_counts,
    iris_in_query_text,
    is_aggregate_sparql,
    orphan_iris,
    owning_module,
    rewrite_select_star,
    tboxes_without_om2,
)

EX = Namespace("http://example.org/health#")
INST = Namespace("https://www.theworldavatar.com/kg/instance/test/")
ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
OM2 = Namespace("http://www.ontology-of-units-of-measure.org/resource/om-2/")


def test_rewrite_select_star_keeps_where_variables() -> None:
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    SELECT DISTINCT ?durationValue ?durationUnit ?durationLabel
    WHERE {
        ?step ontosyn:hasStepDuration ?durationUri .
        OPTIONAL { ?durationUri om-2:hasNumericalValue ?durationValue }
    }
    """
    rewritten = rewrite_select_star(query)
    assert "SELECT DISTINCT * " in rewritten
    assert "?durationUri" in rewritten
    assert rewrite_select_star("ASK { ?s ?p ?o }").lstrip().startswith("ASK")
    grouped = """
    SELECT ?step (MIN(?labelCanon) AS ?label)
    WHERE { ?synthesis ontosyn:hasSynthesisStep ?step }
    GROUP BY ?step
    """
    assert is_aggregate_sparql(grouped)
    assert rewrite_select_star(grouped) == grouped
    assert not is_aggregate_sparql(query)


def test_iris_in_query_text() -> None:
    query = """
    SELECT DISTINCT ?uri WHERE {
      <https://example.org/synth> ontosyn:hasChemicalOutput ?uri .
    }
    """
    iris = iris_in_query_text(query)
    assert URIRef("https://example.org/synth") in iris


def test_where_only_variable_is_covered_and_isolated_node_is_orphan(
    tmp_path: Path,
) -> None:
    tbox = Graph()
    tbox.add((EX.Root, RDF.type, OWL.Class))
    tbox.add((EX.Duration, RDF.type, OWL.Class))
    tbox.add((EX.Equipment, RDF.type, OWL.Class))
    tbox.add((EX.hasDuration, RDF.type, OWL.ObjectProperty))
    tbox_path = tmp_path / "tbox.ttl"
    tbox_path.write_text(tbox.serialize(format="turtle"), encoding="utf-8")

    root = INST.root
    duration = INST.duration
    isolated = INST.isolated
    abox = Graph()
    abox.add((root, RDF.type, EX.Root))
    abox.add((duration, RDF.type, EX.Duration))
    abox.add((isolated, RDF.type, EX.Equipment))
    abox.add((root, EX.hasDuration, duration))
    abox.add((duration, RDFS.label, Literal("1 h")))

    query = """
    SELECT DISTINCT ?label WHERE {
        ?root <http://example.org/health#hasDuration> ?durationUri .
        OPTIONAL { ?durationUri <http://www.w3.org/2000/01/rdf-schema#label> ?label }
    }
    """
    with collect_sparql_coverage() as covered:
        list(abox.query(query, initBindings={"root": root}))

    from src.kg_building.abox_health.graph_index import vocabulary_iris

    vocab = vocabulary_iris(tbox)
    orphans = orphan_iris(abox, covered, vocab)
    assert duration in covered
    assert root in covered
    assert isolated in orphans
    assert duration not in orphans


def test_group_by_query_still_returns_rows_to_caller() -> None:
    graph = Graph()
    step = INST.heat
    graph.add((step, RDF.type, ONTOSYN.HeatChill))
    graph.add((INST.syn, RDF.type, ONTOSYN.ChemicalSynthesis))
    graph.add((INST.syn, ONTOSYN.hasSynthesisStep, step))
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    SELECT ?step (MIN(?order) AS ?ord)
    WHERE {
      ?synthesis a ontosyn:ChemicalSynthesis ;
                 ontosyn:hasSynthesisStep ?step .
      OPTIONAL { ?step ontosyn:hasOrder ?order }
    }
    GROUP BY ?step
    """
    with collect_sparql_coverage() as covered:
        rows = list(graph.query(query))
    assert len(rows) == 1
    assert rows[0].step == step
    assert step in covered


def test_owning_module_is_exclusive() -> None:
    assert owning_module(["LabEquipment", "Equipment"]) is None
    assert owning_module(["Supplier", "Document"]) is None
    assert owning_module(["HeatChill", "SynthesisStep"]) == "steps"
    assert owning_module(["Duration"]) == "steps"
    assert owning_module(["ChemicalInput"]) == "chemicals"
    assert owning_module(["Species", "ChemicalInput"]) == "characterisation"
    assert owning_module(["MetalOrganicPolyhedron", "ChemicalInput"]) == "cbu"
    assert owning_module(["WeightPercentage", "ElementalAnalysisData"]) is None
    assert owning_module(["InfraredBand", "ChemicalShift", "ChemicalFormula"]) is None
    assert owning_module(["HNMRData"]) == "characterisation"


def test_scored_orphans_skip_unscored_types(tmp_path: Path) -> None:
    tbox = Graph()
    tbox.add((EX.Add, RDF.type, OWL.Class))
    tbox.add((EX.LabEquipment, RDF.type, OWL.Class))
    tbox.add((EX.ChemicalInput, RDF.type, OWL.Class))
    tbox.add((EX.MetalOrganicPolyhedron, RDF.type, OWL.Class))
    tbox.add((EX.WeightPercentage, RDF.type, OWL.Class))
    tbox_path = tmp_path / "tbox.ttl"
    tbox_path.write_text(tbox.serialize(format="turtle"), encoding="utf-8")

    add = INST.add
    equipment = INST.eq
    chemical = INST.chem
    mop = INST.mop
    abox = Graph()
    abox.add((add, RDF.type, EX.Add))
    abox.add((equipment, RDF.type, EX.LabEquipment))
    abox.add((chemical, RDF.type, EX.ChemicalInput))
    abox.add((mop, RDF.type, EX.MetalOrganicPolyhedron))
    abox.add((INST.wp, RDF.type, EX.WeightPercentage))

    from src.kg_building.abox_health.graph_index import vocabulary_iris

    vocab = vocabulary_iris(tbox)
    covered: set = set()
    step_orphans = orphan_iris(abox, covered, vocab, scored_modules=["steps"])
    chem_orphans = orphan_iris(abox, covered, vocab, scored_modules=["chemicals"])
    cbu_orphans = orphan_iris(abox, covered, vocab, scored_modules=["cbu"])
    char_orphans = orphan_iris(abox, covered, vocab, scored_modules=["characterisation"])
    assert step_orphans == {add}
    assert chem_orphans == {chemical}
    assert cbu_orphans == {mop}
    assert INST.wp not in char_orphans
    assert equipment not in step_orphans
    assert mop not in step_orphans
    assert mop not in chem_orphans


def test_process_medium_chemical_input_is_not_chemicals_orphan(tmp_path: Path) -> None:
    tbox = Graph()
    tbox.add((EX.ChemicalInput, RDF.type, OWL.Class))
    tbox.add((EX.hasWashingSolvent, RDF.type, OWL.ObjectProperty))
    tbox.add((EX.hasSeparationSolvent, RDF.type, OWL.ObjectProperty))
    tbox.add((EX.hasAddedChemicalInput, RDF.type, OWL.ObjectProperty))
    tbox_path = tmp_path / "tbox.ttl"
    tbox_path.write_text(tbox.serialize(format="turtle"), encoding="utf-8")

    wash = INST.wash
    separate = INST.sep_solv
    added = INST.added
    isolated = INST.isolated_input
    abox = Graph()
    abox.add((wash, RDF.type, EX.ChemicalInput))
    abox.add((separate, RDF.type, EX.ChemicalInput))
    abox.add((added, RDF.type, EX.ChemicalInput))
    abox.add((isolated, RDF.type, EX.ChemicalInput))
    abox.add((INST.filt, EX.hasWashingSolvent, wash))
    abox.add((INST.sep, EX.hasSeparationSolvent, separate))
    abox.add((INST.add, EX.hasAddedChemicalInput, added))

    from src.kg_building.abox_health.graph_index import vocabulary_iris

    vocab = vocabulary_iris(tbox)
    chem_orphans = orphan_iris(abox, set(), vocab, scored_modules=["chemicals"])
    assert wash not in chem_orphans
    assert separate not in chem_orphans
    assert added in chem_orphans
    assert isolated in chem_orphans


def test_f1_orphan_penalty() -> None:
    official = f1_from_counts(10, 1, 1)
    penalized = f1_from_counts(10, 1 + 3, 1)
    assert official > penalized
    assert abs(official - 2 * 10 / (20 + 1 + 1)) < 1e-12


def test_tboxes_without_om2(tmp_path: Path) -> None:
    om2 = tmp_path / "om2.ttl"
    domain = tmp_path / "ontosynthesis.ttl"
    om2.write_text("", encoding="utf-8")
    domain.write_text("", encoding="utf-8")
    kept = tboxes_without_om2([domain, om2])
    assert kept == [domain]
