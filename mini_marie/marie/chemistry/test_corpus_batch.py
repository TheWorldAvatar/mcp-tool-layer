"""Batch unit tests for chemistry corpus stores."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from mini_marie.marie.chemistry.ontokin_corpus import OntokinCorpusStore
from mini_marie.marie.chemistry.provenance_corpus import ProvenanceCorpusStore
from mini_marie.marie.chemistry.species_pka_corpus import SpeciesPkaCorpusStore
from mini_marie.marie.chemistry.species_uses_corpus import SpeciesUsesCorpusStore
from mini_marie.marie.chemistry.zeolite_corpus import ZeoliteCorpusStore


class TestSpeciesPkaCorpus(unittest.TestCase):
    def test_query_pka(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "pka.sqlite")
            store = SpeciesPkaCorpusStore(db)
            try:
                store.upsert_rows(
                    [
                        {
                            "species_iri": "http://ex/s1",
                            "pka_iri": "http://ex/p1",
                            "pka_value": "4.5",
                            "temperature": "",
                            "ionic_strength": "",
                            "method": "",
                            "reliability": "",
                            "acidity_label": "",
                            "provenance": "http://ex/ref1",
                        }
                    ]
                )
                rows = store.query_pka(limit=5)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["provenance"], "http://ex/ref1")
            finally:
                store.close()


class TestSpeciesUsesCorpus(unittest.TestCase):
    @patch("mini_marie.marie.chemistry.species_uses_corpus.fetch_uses_rows_with_retry")
    @patch("mini_marie.marie.chemistry.species_uses_corpus.count_species_remote")
    def test_warm_batch(self, mock_count, mock_fetch):
        mock_count.return_value = 10
        mock_fetch.return_value = (
            [{"species_iri": "http://ex/s1", "use_value": "solvent", "use_value_lc": "solvent"}],
            ["http://ex/s1"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = SpeciesUsesCorpusStore(os.path.join(tmp, "uses.sqlite"))
            try:
                batch = store.warm_batch(batch_size=5, total_species=10)
                self.assertEqual(batch.species_in_batch, 1)
            finally:
                store.close()


class TestZeoliteCorpus(unittest.TestCase):
    def test_upsert_guest_formula(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ZeoliteCorpusStore(os.path.join(tmp, "zeo.sqlite"))
            try:
                store._upsert_material_row(
                    {
                        "material": "http://ex/m1",
                        "label": "test",
                        "frameworkCode": "FAU",
                        "hasGuestFormula": "H2S",
                    },
                    time.time(),
                )
                rows = store._conn.execute(
                    "SELECT property_value FROM corpus_zeolite_properties WHERE property_local='hasGuestFormula'"
                ).fetchall()
                self.assertEqual(len(rows), 1)
            finally:
                store.close()


class TestOntokinCorpus(unittest.TestCase):
    def test_traverse_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = OntokinCorpusStore(os.path.join(tmp, "kin.sqlite"))
            try:
                store._conn.execute(
                    """
                    INSERT INTO corpus_reaction_edges
                    (mechanism_iri, mechanism_label, reaction_iri, equation, equation_lc)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("http://ex/m1", "mech1", "http://ex/r1", "O2 + M = O + OM", "o2 + m = o + om"),
                )
                store._conn.commit()
                rows = store.traverse_reactions(reaction_fragment="O2", limit=10)
                self.assertEqual(len(rows), 1)
            finally:
                store.close()


class TestProvenanceCorpus(unittest.TestCase):
    def test_lookup_ref(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProvenanceCorpusStore(os.path.join(tmp, "prov.sqlite"))
            try:
                store._conn.execute(
                    """
                    INSERT INTO corpus_species_references (ref_iri, label_value, label_value_lc)
                    VALUES (?, ?, ?)
                    """,
                    ("http://ex/ref1", "Perrin 1965", "perrin 1965"),
                )
                store._conn.commit()
                hits = store.lookup_ref_label("Perrin", limit=5)
                self.assertEqual(len(hits), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
