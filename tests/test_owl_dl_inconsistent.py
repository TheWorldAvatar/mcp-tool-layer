from __future__ import annotations

from pathlib import Path

from rdflib import Graph, OWL, RDF
from rdflib import Namespace

from src.kg_building.abox_health.reasoners import check_owl_dl

EX = Namespace("http://example.org/health#")


def test_owlready_inconsistent_error_is_inconsistent(monkeypatch, tmp_path: Path) -> None:
    import owlready2
    from owlready2.base import OwlReadyInconsistentOntologyError

    def boom(*_args, **_kwargs):
        raise OwlReadyInconsistentOntologyError()

    monkeypatch.setattr(owlready2, "sync_reasoner", boom)

    tbox = Graph()
    tbox.add((EX.Root, RDF.type, OWL.Class))
    tbox_path = tmp_path / "tbox.ttl"
    tbox.serialize(tbox_path, format="turtle")

    findings = check_owl_dl(Graph(), tbox_paths=[tbox_path])
    assert len(findings) == 1
    assert findings[0].code == "OWL_DL_INCONSISTENT"
