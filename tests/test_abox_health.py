from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from src.kg_building.abox_health import check_abox, check_ttl_file
from src.kg_building.abox_health.check import discover_run_ttl, resolve_ontology_tboxes
from src.kg_building.abox_health.graph_index import infer_root_iris, load_tbox, subclass_superclasses, vocabulary_iris

ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
BIBO = Namespace("http://purl.org/ontology/bibo/")
EX = Namespace("http://example.org/health#")
INST = Namespace("https://www.theworldavatar.com/kg/instance/test/")

REPO = Path(__file__).resolve().parents[1]
ONTOSYN_TBOX = REPO / "data" / "ontologies" / "ontosynthesis.ttl"
OM2_TBOX = REPO / "data" / "ontologies" / "om2.ttl"
ONTOSYN_SHACL = (
    REPO / "src" / "kg_building" / "ontologx" / "resources" / "ontosynthesis_shacl.ttl"
)


def _codes(report) -> set[str]:
    return {item.code for item in report.errors()}


def _mini_tbox() -> Graph:
    g = Graph()
    g.add((EX.Root, RDF.type, OWL.Class))
    g.add((EX.Child, RDF.type, OWL.Class))
    g.add((EX.Other, RDF.type, OWL.Class))
    g.add((EX.hasChild, RDF.type, OWL.ObjectProperty))
    g.add((EX.hasChild, RDFS.domain, EX.Root))
    g.add((EX.hasChild, RDFS.range, EX.Child))
    g.add((EX.pointsAt, RDF.type, OWL.ObjectProperty))
    g.add((EX.pointsAt, RDFS.domain, EX.Other))
    g.add((EX.pointsAt, RDFS.range, EX.Root))
    g.add((EX.flag, RDF.type, OWL.DatatypeProperty))
    g.add((EX.flag, RDFS.domain, EX.Root))
    g.add((EX.flag, RDFS.range, XSD.boolean))
    g.add((EX.A, RDF.type, OWL.Class))
    g.add((EX.B, RDF.type, OWL.Class))
    g.add((EX.A, OWL.disjointWith, EX.B))
    return g


def _write_ttl(path: Path, graph: Graph) -> Path:
    path.write_text(graph.serialize(format="turtle"), encoding="utf-8")
    return path


def _healthy_ontosyn() -> Graph:
    g = Graph()
    syn = INST.syn
    doc = INST.doc
    out = INST.out
    add = INST.add
    inp = INST.inp
    g.add((syn, RDF.type, ONTOSYN.ChemicalSynthesis))
    g.add((syn, RDFS.label, Literal("MOCK-SYN")))
    g.add((syn, ONTOSYN.hasChemicalOutput, out))
    g.add((syn, ONTOSYN.hasSynthesisStep, add))
    g.add((syn, ONTOSYN.retrievedFrom, doc))
    g.add((doc, RDF.type, BIBO.Document))
    g.add((doc, RDFS.label, Literal("paper")))
    g.add((out, RDF.type, ONTOSYN.ChemicalOutput))
    g.add((out, RDFS.label, Literal("product")))
    g.add((add, RDF.type, ONTOSYN.Add))
    g.add((add, RDFS.label, Literal("Add")))
    g.add((add, ONTOSYN.hasOrder, Literal(1, datatype=XSD.integer)))
    g.add((add, ONTOSYN.hasAddedChemicalInput, inp))
    g.add((inp, RDF.type, ONTOSYN.ChemicalInput))
    g.add((inp, RDFS.label, Literal("input")))
    return g


def test_healthy_mini_graph_has_no_orphans(tmp_path: Path) -> None:
    tbox_path = _write_ttl(tmp_path / "tbox.ttl", _mini_tbox())
    abox = Graph()
    root = INST.root
    child = INST.child
    abox.add((root, RDF.type, EX.Root))
    abox.add((child, RDF.type, EX.Child))
    abox.add((root, EX.hasChild, child))
    report = check_abox(
        abox,
        tbox_paths=[tbox_path],
        roots=[str(root)],
        run_shacl=False,
        run_owlrl=False,
        run_owl_dl=False,
    )
    assert report.ok
    assert report.instance_count == 2
    assert not report.errors()


def test_isolated_and_incoming_only_orphans(tmp_path: Path) -> None:
    tbox_path = _write_ttl(tmp_path / "tbox.ttl", _mini_tbox())
    abox = Graph()
    root = INST.root
    child = INST.child
    isolated = INST.isolated
    satellite = INST.satellite
    dangling = URIRef("http://example.org/missing-node")
    abox.add((root, RDF.type, EX.Root))
    abox.add((child, RDF.type, EX.Child))
    abox.add((root, EX.hasChild, child))
    abox.add((isolated, RDF.type, EX.Other))
    abox.add((satellite, RDF.type, EX.Other))
    abox.add((satellite, EX.pointsAt, root))
    island_a = INST.island_a
    island_b = INST.island_b
    abox.add((island_a, RDF.type, EX.Other))
    abox.add((island_b, RDF.type, EX.Other))
    abox.add((island_a, EX.pointsAt, island_b))
    abox.add((root, EX.hasChild, dangling))
    report = check_abox(
        abox,
        tbox_paths=[tbox_path],
        roots=[str(root)],
        run_shacl=False,
        run_owlrl=False,
        run_owl_dl=False,
    )
    codes = _codes(report)
    assert "ISOLATED_TYPED_NODE" in codes
    assert "INCOMING_ONLY" in codes
    assert "DIRECTED_UNREACHABLE" in codes
    assert "DANGLING_OBJECT_IRI" in codes
    assert "WEAKLY_DISCONNECTED" in codes
    iris = {iri for item in report.errors() for iri in item.iris}
    assert str(isolated) in iris
    assert str(satellite) in iris
    assert str(dangling) in iris
    assert str(INST.island_a) in iris
    assert not report.ok


def test_domain_violation_on_wrong_subject(tmp_path: Path) -> None:
    tbox_path = _write_ttl(tmp_path / "tbox.ttl", _mini_tbox())
    abox = Graph()
    root = INST.root
    child = INST.child
    abox.add((root, RDF.type, EX.Root))
    abox.add((child, RDF.type, EX.Child))
    abox.add((root, EX.hasChild, child))
    abox.add((child, EX.hasChild, child))
    report = check_abox(
        abox,
        tbox_paths=[tbox_path],
        roots=[str(root)],
        run_shacl=False,
        run_owlrl=False,
        run_owl_dl=False,
    )
    assert "DOMAIN_VIOLATION" in _codes(report)


def test_disjoint_types_from_tbox(tmp_path: Path) -> None:
    tbox_path = _write_ttl(tmp_path / "tbox.ttl", _mini_tbox())
    abox = Graph()
    root = INST.root
    clash = INST.clash
    abox.add((root, RDF.type, EX.Root))
    abox.add((clash, RDF.type, EX.A))
    abox.add((clash, RDF.type, EX.B))
    abox.add((root, EX.hasChild, clash))
    report = check_abox(
        abox,
        tbox_paths=[tbox_path],
        roots=[str(root)],
        run_shacl=False,
        run_owlrl=False,
        run_owl_dl=False,
    )
    assert "DISJOINT_TYPES" in _codes(report)


def test_owlrl_runs_without_nothing_on_healthy_graph(tmp_path: Path) -> None:
    tbox_path = _write_ttl(tmp_path / "tbox.ttl", _mini_tbox())
    abox = Graph()
    root = INST.root
    child = INST.child
    abox.add((root, RDF.type, EX.Root))
    abox.add((child, RDF.type, EX.Child))
    abox.add((root, EX.hasChild, child))
    report = check_abox(
        abox,
        tbox_paths=[tbox_path],
        roots=[str(root)],
        run_shacl=False,
        run_owlrl=True,
        run_owl_dl=False,
    )
    assert "OWL_RL_NOTHING" not in _codes(report)


def test_ontosynthesis_healthy_graph_passes_shacl() -> None:
    if not ONTOSYN_TBOX.is_file() or not ONTOSYN_SHACL.is_file():
        return
    report = check_abox(
        _healthy_ontosyn(),
        tbox_paths=[ONTOSYN_TBOX, OM2_TBOX] if OM2_TBOX.is_file() else [ONTOSYN_TBOX],
        shacl_path=ONTOSYN_SHACL,
        roots=[str(INST.syn)],
        run_owlrl=False,
        run_owl_dl=False,
    )
    assert "DIRECTED_UNREACHABLE" not in _codes(report)
    assert "SHACL_VIOLATION" not in _codes(report)
    assert report.ok


def test_ontosynthesis_add_without_input_fails_shacl() -> None:
    g = _healthy_ontosyn()
    g.remove((INST.add, ONTOSYN.hasAddedChemicalInput, INST.inp))
    g.remove((INST.inp, RDF.type, ONTOSYN.ChemicalInput))
    g.remove((INST.inp, RDFS.label, Literal("input")))
    report = check_abox(
        g,
        tbox_paths=[ONTOSYN_TBOX, OM2_TBOX] if OM2_TBOX.is_file() else [ONTOSYN_TBOX],
        shacl_path=ONTOSYN_SHACL,
        roots=[str(INST.syn)],
        run_owlrl=False,
        run_owl_dl=False,
    )
    assert "SHACL_VIOLATION" in _codes(report)


def test_ontosynthesis_missing_retrieved_from_hits_owl_restriction() -> None:
    g = _healthy_ontosyn()
    g.remove((INST.syn, ONTOSYN.retrievedFrom, INST.doc))
    report = check_abox(
        g,
        tbox_paths=[ONTOSYN_TBOX],
        roots=[str(INST.syn)],
        run_shacl=False,
        run_owlrl=False,
        run_owl_dl=False,
    )
    assert "OWL_RESTRICTION" in _codes(report)


def test_ontosynthesis_wrong_domain_has_added_chemical() -> None:
    g = _healthy_ontosyn()
    g.remove((INST.add, RDF.type, ONTOSYN.Add))
    g.add((INST.add, RDF.type, ONTOSYN.Stir))
    g.remove((INST.add, ONTOSYN.hasAddedChemicalInput, INST.inp))
    g.add((INST.add, ONTOSYN.hasAddedChemicalInput, INST.inp))
    report = check_abox(
        g,
        tbox_paths=[ONTOSYN_TBOX],
        roots=[str(INST.syn)],
        run_shacl=False,
        run_owlrl=False,
        run_owl_dl=False,
    )
    assert "DOMAIN_VIOLATION" in _codes(report)


def test_identity_sidecar_supplies_root(tmp_path: Path) -> None:
    tbox_path = _write_ttl(tmp_path / "tbox.ttl", _mini_tbox())
    abox = Graph()
    root = INST.root
    child = INST.child
    abox.add((root, RDF.type, EX.Root))
    abox.add((child, RDF.type, EX.Child))
    abox.add((root, EX.hasChild, child))
    ttl = tmp_path / "entity.ttl"
    _write_ttl(ttl, abox)
    ttl.with_name("entity.identity.json").write_text(
        '{"identity": {"uri": "%s", "types": ["%s"]}}' % (root, EX.Root),
        encoding="utf-8",
    )
    report = check_ttl_file(
        ttl,
        tbox_paths=[tbox_path],
        run_shacl=False,
        run_owlrl=False,
        run_owl_dl=False,
    )
    assert report.roots == [str(root)]
    assert report.ok


def test_infer_root_uses_chemical_synthesis() -> None:
    tbox = load_tbox([ONTOSYN_TBOX])
    vocab = vocabulary_iris(tbox)
    closure = subclass_superclasses(tbox)
    abox = _healthy_ontosyn()
    roots = infer_root_iris(abox, tbox=tbox, vocab=vocab, closure=closure)
    assert str(INST.syn) in roots
    assert str(INST.add) not in roots


def test_discover_run_ttl_finds_pipeline_and_ox_outputs(tmp_path: Path) -> None:
    pipe = tmp_path / "pipe" / "runtime" / "abc" / "ontosynthesis_output"
    ox = tmp_path / "ox" / "abc" / "ontosynthesis_output"
    pipe.mkdir(parents=True)
    ox.mkdir(parents=True)
    (pipe / "UMC-1.ttl").write_text("# pipe", encoding="utf-8")
    (ox / "UMC-1.ttl").write_text("# ox", encoding="utf-8")
    merged = tmp_path / "s1p30" / "merged" / "0c57bac8"
    merged.mkdir(parents=True)
    (merged / "0c57bac8.ttl").write_text("# merged", encoding="utf-8")
    (merged / "link.ttl").write_text("# skip", encoding="utf-8")
    found = discover_run_ttl(tmp_path)
    names = {path.name for path in found}
    assert "UMC-1.ttl" in names
    assert "0c57bac8.ttl" in names
    assert "link.ttl" not in names
    assert len(found) == 3


def test_resolve_ontology_tboxes_ontosynthesis() -> None:
    tboxes, shacl = resolve_ontology_tboxes("ontosynthesis")
    assert tboxes[0].name == "ontosynthesis.ttl"
    assert shacl is not None
    assert shacl.name == "ontosynthesis_shacl.ttl"


def test_resolve_ontology_tboxes_medical() -> None:
    tboxes, shacl = resolve_ontology_tboxes("medical")
    assert tboxes[0].name == "medical_case_schema_de_non_flat_v4.ttl"
    assert shacl is not None
    assert shacl.name == "medical_shacl.ttl"


def test_has_equipment_labequipment_is_not_a_shacl_class_error() -> None:
    ontolab = Namespace("https://www.theworldavatar.com/kg/OntoLab/")
    g = _healthy_ontosyn()
    g.add((INST.syn, ONTOSYN.hasEquipment, INST.eq))
    g.add((INST.eq, RDF.type, ontolab.LabEquipment))
    g.add((INST.eq, RDFS.label, Literal("autoclave")))
    report = check_abox(
        g,
        tbox_paths=[ONTOSYN_TBOX, OM2_TBOX],
        shacl_path=ONTOSYN_SHACL,
        roots=[str(INST.syn)],
        run_shacl=True,
        run_owlrl=False,
        run_owl_dl=False,
    )
    has_equipment_class_hits = [
        item
        for item in report.errors()
        if item.code == "SHACL_VIOLATION"
        and any(str(ONTOSYN.hasEquipment) == iri for iri in item.iris)
    ]
    assert has_equipment_class_hits == []
