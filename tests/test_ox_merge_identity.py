from __future__ import annotations

import sys
from pathlib import Path

OX = Path(__file__).resolve().parents[1] / "src" / "kg_building" / "ontologx"
if str(OX) not in sys.path:
    sys.path.insert(0, str(OX))

from rdflib import Graph

from graph_merge import (
    attach_subgraph,
    identity_key,
    is_cross_document_reusable,
    reusable_subgraph,
    scope_occurrence_ids,
)
from graph_types import GraphDocument, Node, Relationship
from publish_runtime import publish_spliced_runtime
from ttl_export import graph_to_rdflib, write_ttl


def _graph(*nodes, rels=()):
    by_id = {node.id: node for node in nodes}
    relationships = [
        Relationship(source=by_id[src], target=by_id[tgt], type=typ) for src, typ, tgt in rels
    ]
    return GraphDocument(nodes=list(nodes), relationships=relationships, source=None)


def test_paper_attach_does_not_collapse_chemical_output_ids():
    first = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "UMC-1"}),
        Node(id="ChemicalOutput-1", type="ontosyn:ChemicalOutput", properties={"rdfs:label": "UMC-1"}),
        rels=(("cs1", "ontosyn:hasChemicalOutput", "ChemicalOutput-1"),),
    )
    second = _graph(
        Node(id="cs2", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "UMC-2"}),
        Node(id="ChemicalOutput-1", type="ontosyn:ChemicalOutput", properties={"rdfs:label": "UMC-2"}),
        rels=(("cs2", "ontosyn:hasChemicalOutput", "ChemicalOutput-1"),),
    )
    merged = attach_subgraph(first, second, reuse="paper")
    outputs = [node for node in merged.nodes if node.type == "ontosyn:ChemicalOutput"]
    assert len(outputs) == 2
    labels = {str((node.properties or {}).get("rdfs:label")) for node in outputs}
    assert labels == {"UMC-1", "UMC-2"}
    assert identity_key(outputs[0], reuse="paper") != identity_key(outputs[1], reuse="paper")


def test_scope_occurrence_ids_keeps_reusable_and_rewrites_absolute_owners():
    graph = _graph(
        Node(
            id="https://www.theworldavatar.com/kg/instance/generated/root1",
            type="ontosyn:ChemicalSynthesis",
            properties={"rdfs:label": "UMC-1"},
        ),
        Node(
            id="https://www.theworldavatar.com/kg/instance/ontologx/0c57bac8/ChemicalOutput-1",
            type="ontosyn:ChemicalOutput",
            properties={"rdfs:label": "UMC-1"},
        ),
        Node(
            id="https://www.theworldavatar.com/kg/instance/generated/Document-1",
            type="bibo:Document",
            properties={"rdfs:label": "10.x"},
        ),
        rels=(
            (
                "https://www.theworldavatar.com/kg/instance/generated/root1",
                "ontosyn:hasChemicalOutput",
                "https://www.theworldavatar.com/kg/instance/ontologx/0c57bac8/ChemicalOutput-1",
            ),
        ),
    )
    scoped = scope_occurrence_ids(
        graph, "https://www.theworldavatar.com/kg/instance/generated/root1"
    )
    ids = {node.id for node in scoped.nodes}
    assert "https://www.theworldavatar.com/kg/instance/generated/root1" in ids
    assert "https://www.theworldavatar.com/kg/instance/generated/Document-1" in ids
    assert "root1__ChemicalOutput-1" in ids
    assert "ChemicalOutput-1" not in ids
    assert not any(item.endswith("/ChemicalOutput-1") for item in ids)


def test_ccdc_mop_is_not_cross_document_reusable():
    mop = Node(
        id="ontomops:MetalOrganicPolyhedron-1",
        type="ontomops:MetalOrganicPolyhedron",
        properties={"rdfs:label": "Zr-bpydc-CuCl2", "ontomops:hasCCDCNumber": "1469174"},
    )
    assert is_cross_document_reusable(mop) is False
    paper = _graph(mop)
    assert reusable_subgraph(paper, scope="global") is None


def test_write_ttl_namespaces_occurrence_ids(tmp_path: Path):
    first = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "VMOP-1"}),
        Node(
            id="https://www.theworldavatar.com/kg/instance/ontologx/73a6d32b/ChemicalOutput-1",
            type="ontosyn:ChemicalOutput",
            properties={"rdfs:label": "VMOP-1"},
        ),
        rels=(
            (
                "cs1",
                "ontosyn:hasChemicalOutput",
                "https://www.theworldavatar.com/kg/instance/ontologx/73a6d32b/ChemicalOutput-1",
            ),
        ),
    )
    second = _graph(
        Node(id="cs2", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "VMOP-2"}),
        Node(
            id="https://www.theworldavatar.com/kg/instance/ontologx/73a6d32b/ChemicalOutput-1",
            type="ontosyn:ChemicalOutput",
            properties={"rdfs:label": "VMOP-2"},
        ),
        rels=(
            (
                "cs2",
                "ontosyn:hasChemicalOutput",
                "https://www.theworldavatar.com/kg/instance/ontologx/73a6d32b/ChemicalOutput-1",
            ),
        ),
    )
    out = tmp_path / "out"
    write_ttl(
        first,
        "73a6d32b",
        out / "a.ttl",
        scope="https://www.theworldavatar.com/kg/instance/generated/lAWwmQU2RsmL14j1",
    )
    write_ttl(
        second,
        "73a6d32b",
        out / "b.ttl",
        scope="https://www.theworldavatar.com/kg/instance/generated/JETxo-Z6TymJCc-a",
    )
    text_a = (out / "a.ttl").read_text(encoding="utf-8")
    text_b = (out / "b.ttl").read_text(encoding="utf-8")
    assert "lAWwmQU2RsmL14j1__ChemicalOutput-1" in text_a
    assert "JETxo-Z6TymJCc-a__ChemicalOutput-1" in text_b
    assert "/ChemicalOutput-1>" not in text_a
    assert "/ChemicalOutput-1>" not in text_b


def test_publish_without_extensions_is_pipeline_shaped(tmp_path: Path):
    umc1 = _graph(
        Node(id="cs1", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "UMC-1"}),
        Node(id="ChemicalOutput-1", type="ontosyn:ChemicalOutput", properties={"rdfs:label": "UMC-1"}),
        rels=(("cs1", "ontosyn:hasChemicalOutput", "ChemicalOutput-1"),),
    )
    umc2 = _graph(
        Node(id="cs2", type="ontosyn:ChemicalSynthesis", properties={"rdfs:label": "UMC-2"}),
        Node(id="ChemicalOutput-1", type="ontosyn:ChemicalOutput", properties={"rdfs:label": "UMC-2"}),
        rels=(("cs2", "ontosyn:hasChemicalOutput", "ChemicalOutput-1"),),
    )
    dest = tmp_path / "score_runtime"
    publish_spliced_runtime(
        dest_root=dest,
        paper_hash="0c57bac8",
        entity_label="UMC-1",
        spliced=umc1,
        main=umc1,
        write_extensions=False,
    )
    publish_spliced_runtime(
        dest_root=dest,
        paper_hash="0c57bac8",
        entity_label="UMC-2",
        spliced=umc2,
        main=umc2,
        write_extensions=False,
    )
    paper_dir = dest / "0c57bac8"
    assert (paper_dir / "ontosynthesis_output" / "UMC-1.ttl").is_file()
    assert (paper_dir / "ontosynthesis_output" / "UMC-2.ttl").is_file()
    assert not (paper_dir / "ontospecies_output").exists()
    assert not (paper_dir / "ontomops_output").exists()
    rdf1 = graph_to_rdflib(scope_occurrence_ids(umc1, "UMC-1"), "0c57bac8")
    rdf2 = graph_to_rdflib(scope_occurrence_ids(umc2, "UMC-2"), "0c57bac8")
    iris1 = {str(s) for s in rdf1.subjects()}
    iris2 = {str(s) for s in rdf2.subjects()}
    assert not any("ChemicalOutput-1" in iri and "UMC-1__" not in iri for iri in iris1)
    assert iris1.isdisjoint(iris2)


def test_publish_medical_output_dir(tmp_path: Path):
    case = _graph(
        Node(id="mc1", type="medical:MedicalCase", properties={"rdfs:label": "OPR1a"}),
    )
    dest = tmp_path / "score_runtime"
    paths = publish_spliced_runtime(
        dest_root=dest,
        paper_hash="23a00605",
        entity_label="OPR1a",
        spliced=case,
        main=case,
        write_extensions=False,
        main_output_dir="medical_output",
    )
    paper_dir = dest / "23a00605"
    assert (paper_dir / "medical_output" / "OPR1a.ttl").is_file()
    assert not (paper_dir / "ontosynthesis_output").exists()
    assert "ontosynthesis" not in paths
    assert Path(paths["main"]).is_file()


def test_publish_three_syntheses_keep_distinct_outputs_and_shared_document(tmp_path: Path):
    document = Node(
        id="https://www.theworldavatar.com/kg/instance/generated/Document-1",
        type="bibo:Document",
        properties={"rdfs:label": "73a6d32b"},
    )
    dest = tmp_path / "score_runtime"
    hashes = []
    for slug, label, token in (
        ("cs1", "VMOP-1", "lAWwmQU2RsmL14j1"),
        ("cs2", "VMOP-2", "JETxo-Z6TymJCc-a"),
        ("cs3", "VMOP-3", "faSip9jZSouq5yQO"),
    ):
        graph = _graph(
            Node(
                id=f"https://www.theworldavatar.com/kg/instance/generated/{token}",
                type="ontosyn:ChemicalSynthesis",
                properties={"rdfs:label": label},
            ),
            Node(
                id="https://www.theworldavatar.com/kg/instance/ontologx/73a6d32b/ChemicalOutput-1",
                type="ontosyn:ChemicalOutput",
                properties={"rdfs:label": label},
            ),
            document,
            rels=(
                (
                    f"https://www.theworldavatar.com/kg/instance/generated/{token}",
                    "ontosyn:hasChemicalOutput",
                    "https://www.theworldavatar.com/kg/instance/ontologx/73a6d32b/ChemicalOutput-1",
                ),
            ),
        )
        publish_spliced_runtime(
            dest_root=dest,
            paper_hash="73a6d32b",
            entity_label=label,
            spliced=graph,
            main=graph,
            write_extensions=False,
            entity_uri=f"https://www.theworldavatar.com/kg/instance/generated/{token}",
            slug=slug,
        )
        hashes.append(token)
    out_dir = dest / "73a6d32b" / "ontosynthesis_output"
    files = {path.name for path in out_dir.glob("*.ttl")}
    assert files == {f"{token}.ttl" for token in hashes}
    merged = Graph()
    for path in out_dir.glob("*.ttl"):
        merged.parse(str(path), format="turtle")
    subjects = {str(subject) for subject in merged.subjects()}
    outputs = {iri for iri in subjects if iri.endswith("__ChemicalOutput-1")}
    docs = {iri for iri in subjects if iri.endswith("/Document-1")}
    assert len(outputs) == 3
    assert not any(iri.endswith("/ChemicalOutput-1") for iri in subjects)
    assert len(docs) == 1


def test_publish_slash_label_stays_top_level(tmp_path: Path):
    label = "Synthesis of cage from TMTAH3 in DMF/ethanol with KOH"
    graph = _graph(
        Node(
            id="https://www.theworldavatar.com/kg/instance/generated/Iqe21bPCQ8aoX1MP",
            type="ontosyn:ChemicalSynthesis",
            properties={"rdfs:label": label},
        ),
        Node(id="ChemicalOutput-1", type="ontosyn:ChemicalOutput", properties={"rdfs:label": "1"}),
        rels=(
            (
                "https://www.theworldavatar.com/kg/instance/generated/Iqe21bPCQ8aoX1MP",
                "ontosyn:hasChemicalOutput",
                "ChemicalOutput-1",
            ),
        ),
    )
    dest = tmp_path / "score_runtime"
    publish_spliced_runtime(
        dest_root=dest,
        paper_hash="3f239659",
        entity_label=label,
        spliced=graph,
        main=graph,
        write_extensions=False,
        entity_uri="https://www.theworldavatar.com/kg/instance/generated/Iqe21bPCQ8aoX1MP",
        slug="cs1",
    )
    out_dir = dest / "3f239659" / "ontosynthesis_output"
    files = list(out_dir.glob("*.ttl"))
    assert files == [out_dir / "Iqe21bPCQ8aoX1MP.ttl"]
    assert not any(path.is_dir() for path in out_dir.iterdir())
