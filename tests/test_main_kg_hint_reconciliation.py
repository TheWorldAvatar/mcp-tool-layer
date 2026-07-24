"""Regression tests for publish-time structured-hint reconciliation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rdflib import Graph, Literal, RDF, RDFS, URIRef

from src.pipelines.main_kg_building.build import (
    _repair_published_entity_ttl_from_hints,
)


EX = "https://example.com/"


class TestMainKgHintReconciliation(unittest.TestCase):
    def test_object_label_hint_never_becomes_literal_predicate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="hint_reconcile_") as tmp:
            ttl_path = Path(tmp) / "entity.ttl"
            top = URIRef(f"{EX}synthesis")
            output = URIRef(f"{EX}output")
            mop = URIRef(f"{EX}mop")
            graph = Graph()
            graph.add((top, RDF.type, URIRef(f"{EX}ChemicalSynthesis")))
            graph.add((top, RDFS.label, Literal("UMC-1")))
            graph.add((output, RDF.type, URIRef(f"{EX}ChemicalOutput")))
            graph.add((output, RDFS.label, Literal("UMC-1")))
            graph.add((output, URIRef(f"{EX}isRepresentedBy"), mop))
            graph.add((mop, RDF.type, URIRef(f"{EX}MetalOrganicPolyhedron")))
            graph.add((top, URIRef(f"{EX}hasChemicalOutput"), output))
            graph.serialize(destination=str(ttl_path), format="turtle")

            policy = {
                "shell_validation": {
                    "top_entity_class_iri": f"{EX}ChemicalSynthesis",
                    "required_links": [
                        {
                            "section_name": "ChemicalOutput",
                            "predicate_iri": f"{EX}hasChemicalOutput",
                            "target_class_iri": f"{EX}ChemicalOutput",
                            "property_namespace_iri": EX,
                            "min_count": 1,
                        }
                    ],
                }
            }
            ok, messages = _repair_published_entity_ttl_from_hints(
                ttl_path=str(ttl_path),
                entity_uri=str(top),
                entity_label="UMC-1",
                aggregated_hints={
                    "ChemicalOutput": {
                        "label": "UMC-1",
                        "hasChemicalFormula": "C1",
                        "isRepresentedBy_label": "UMC-1",
                    }
                },
                ontology_name="ontosynthesis",
                main_entity_policy=policy,
            )
            self.assertTrue(ok, msg="\n".join(messages))

            repaired = Graph()
            repaired.parse(str(ttl_path), format="turtle")
            self.assertIn(
                (output, URIRef(f"{EX}hasChemicalFormula"), Literal("C1")),
                repaired,
            )
            self.assertNotIn(
                (output, URIRef(f"{EX}isRepresentedBy_label"), Literal("UMC-1")),
                repaired,
            )
            self.assertIn((output, URIRef(f"{EX}isRepresentedBy"), mop), repaired)


if __name__ == "__main__":
    unittest.main()
