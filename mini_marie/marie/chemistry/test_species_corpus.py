"""Tests for OntoSpecies corpus cache."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from mini_marie.marie.chemistry.species_corpus import SpeciesCorpusStore


class TestSpeciesCorpus(unittest.TestCase):
    def test_upsert_and_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "corpus.sqlite")
            store = SpeciesCorpusStore(db)
            try:
                store.upsert_name_rows(
                    [
                        {
                            "species_iri": "http://ex/s1",
                            "name_type": "label",
                            "name_value": "C6H8O6",
                            "name_value_lc": "c6h8o6",
                        },
                        {
                            "species_iri": "http://ex/s1",
                            "name_type": "iupac",
                            "name_value": "ascorbic acid",
                            "name_value_lc": "ascorbic acid",
                        },
                    ]
                )
                hits = store.search_names("ascorbic", limit=5, fuzzy=True)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["name_value"], "ascorbic acid")
            finally:
                store.close()

    @patch("mini_marie.marie.chemistry.species_corpus.fetch_species_name_rows_with_retry")
    @patch("mini_marie.marie.chemistry.species_corpus.count_species_remote")
    def test_warm_batch_advances_offset(self, mock_count, mock_fetch):
        mock_count.return_value = 100
        mock_fetch.return_value = (
            [
                {
                    "species_iri": "http://ex/a",
                    "name_type": "label",
                    "name_value": "X",
                    "name_value_lc": "x",
                }
            ],
            ["http://ex/a"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "corpus.sqlite")
            store = SpeciesCorpusStore(db)
            try:
                batch = store.warm_batch(batch_size=50, total_species=100)
                self.assertEqual(batch.species_in_batch, 1)
                state = store.warm_state()
                self.assertEqual(state["offset_next"], 1)
                self.assertEqual(state["species_indexed"], 1)
                self.assertEqual(state["cursor_subject"], "http://ex/a")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
