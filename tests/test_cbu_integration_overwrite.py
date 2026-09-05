from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

from src.agents.mops.cbu_derivation.integration import (
    _overwrite_source_cbu_formulas,
    _select_primary_mop,
    _try_heuristic_iris,
    _write_integrated_ttl,
)


ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")

STUB = URIRef("https://example.test/mop-stub")
REAL = URIRef("https://example.test/mop-real")
METAL = URIRef("https://example.test/cbu-metal")
ORGANIC = URIRef("https://example.test/cbu-organic")


def _vmop13_style_ttl() -> str:
    g = Graph()
    g.bind("ontomops", ONTOMOPS)
    g.add((STUB, RDF.type, ONTOMOPS.MetalOrganicPolyhedron))
    g.add((REAL, RDF.type, ONTOMOPS.MetalOrganicPolyhedron))
    g.add((REAL, RDFS.label, Literal("VMOP-13")))
    g.add((REAL, ONTOMOPS.hasCCDCNumber, Literal("1479719")))
    g.add((REAL, ONTOMOPS.hasChemicalBuildingUnit, METAL))
    g.add((REAL, ONTOMOPS.hasChemicalBuildingUnit, ORGANIC))
    g.add((METAL, RDF.type, ONTOMOPS.ChemicalBuildingUnit))
    g.add((METAL, RDFS.label, Literal("[V6O6(OCH3)9(SO4)]")))
    g.add((METAL, ONTOMOPS.hasCBUFormula, Literal("[V6O6(OCH3)9(SO4)]")))
    g.add((ORGANIC, RDF.type, ONTOMOPS.ChemicalBuildingUnit))
    g.add((ORGANIC, RDFS.label, Literal("[BDC-Br]")))
    g.add((ORGANIC, ONTOMOPS.hasCBUFormula, Literal("[BDC-Br]")))
    return g.serialize(format="turtle")


def test_select_primary_mop_skips_empty_stub() -> None:
    graph = Graph()
    graph.parse(data=_vmop13_style_ttl(), format="turtle")

    assert _select_primary_mop(graph, ONTOMOPS) == REAL


def test_heuristic_iris_splits_metal_and_organic_labels() -> None:
    metal_iri, organic_iri = _try_heuristic_iris(
        [
            {"iri": str(METAL), "labels": ["[V6O6(OCH3)9(SO4)]"], "alt_names": [], "formulas": []},
            {"iri": str(ORGANIC), "labels": ["[BDC-Br]"], "alt_names": [], "formulas": ["[BDC-Br]"]},
        ],
        metal_formula="",
        organic_formula="[(C6H3Br)(CO2)2]",
    )

    assert metal_iri == str(METAL)
    assert organic_iri == str(ORGANIC)


def test_overwrite_source_replaces_draft_formula(tmp_path: Path) -> None:
    src = tmp_path / "ontomops_extension_VMOP-13.ttl"
    src.write_text(_vmop13_style_ttl(), encoding="utf-8")

    _overwrite_source_cbu_formulas(
        str(src),
        [(str(ORGANIC), "[(C6H3Br)(CO2)2]")],
    )

    graph = Graph()
    graph.parse(data=src.read_text(encoding="utf-8"), format="turtle")
    formulas = {str(value) for value in graph.objects(ORGANIC, ONTOMOPS.hasCBUFormula)}
    labels = {str(value) for value in graph.objects(ORGANIC, RDFS.label)}
    metal_formulas = {str(value) for value in graph.objects(METAL, ONTOMOPS.hasCBUFormula)}

    assert formulas == {"[(C6H3Br)(CO2)2]"}
    assert "[BDC-Br]" not in labels
    assert "[(C6H3Br)(CO2)2]" in labels
    assert metal_formulas == {"[V6O6(OCH3)9(SO4)]"}


def test_write_integrated_ttl_overwrites_source_and_skips_stub(
    tmp_path: Path,
    monkeypatch,
) -> None:
    hash_value = "abc12345"
    case_dir = tmp_path / hash_value
    ontomops_dir = case_dir / "ontomops_output"
    integrated_dir = case_dir / "cbu_derivation" / "integrated"
    ontomops_dir.mkdir(parents=True)
    integrated_dir.mkdir(parents=True)
    src = ontomops_dir / "ontomops_extension_VMOP-13.ttl"
    src.write_text(_vmop13_style_ttl(), encoding="utf-8")

    monkeypatch.setattr(
        "src.agents.mops.cbu_derivation.integration._optional_root_ttl_path",
        lambda *args, **kwargs: "",
    )

    _write_integrated_ttl(
        hash_value,
        "VMOP-13",
        "ontomops_extension_VMOP-13.ttl",
        {"formula": "", "iri": str(METAL)},
        {"formula": "[(C6H3Br)(CO2)2]", "iri": str(ORGANIC)},
        str(integrated_dir),
        data_dir=str(tmp_path),
    )

    source = Graph()
    source.parse(data=src.read_text(encoding="utf-8"), format="turtle")
    integrated = Graph()
    integrated.parse(
        data=(integrated_dir / "VMOP-13.ttl").read_text(encoding="utf-8"),
        format="turtle",
    )

    assert {str(value) for value in source.objects(ORGANIC, ONTOMOPS.hasCBUFormula)} == {
        "[(C6H3Br)(CO2)2]"
    }
    assert {str(value) for value in integrated.objects(ORGANIC, ONTOMOPS.hasCBUFormula)} == {
        "[(C6H3Br)(CO2)2]"
    }
    assert {str(value) for value in integrated.objects(METAL, ONTOMOPS.hasCBUFormula)} == {
        "[V6O6(OCH3)9(SO4)]"
    }
    assert (STUB, ONTOMOPS.hasChemicalBuildingUnit, ORGANIC) not in integrated
    merged_formulas = {
        str(value)
        for value in list(source.objects(ORGANIC, ONTOMOPS.hasCBUFormula))
        + list(integrated.objects(ORGANIC, ONTOMOPS.hasCBUFormula))
    }
    assert merged_formulas == {"[(C6H3Br)(CO2)2]"}
