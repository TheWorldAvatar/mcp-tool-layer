"""Tests for derived formula index and corpus helpers."""

from __future__ import annotations

import os
import tempfile
import unittest

from mini_marie.marie.chemistry.species_corpus import SpeciesCorpusStore
from mini_marie.marie.chemistry.species_formula_corpus import SpeciesFormulaStore


class TestSpeciesFormulaCorpus(unittest.TestCase):
    def test_build_from_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "corpus.sqlite")
            names = SpeciesCorpusStore(db)
            try:
                names.upsert_name_rows(
                    [
                        {
                            "species_iri": "http://ex/s1",
                            "name_type": "formula",
                            "name_value": "C6H8O6",
                            "name_value_lc": "c6h8o6",
                        }
                    ]
                )
            finally:
                names.close()
            store = SpeciesFormulaStore(db)
            try:
                inserted = store.build_from_names()
                self.assertGreaterEqual(inserted, 1)
                hits = store.lookup_formula("C6H8O6", match="equals")
                self.assertEqual(len(hits), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
