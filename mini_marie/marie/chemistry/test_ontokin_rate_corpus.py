"""Tests for OntoKin rate model corpus."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from mini_marie.marie.chemistry.ontokin_rate_corpus import OntokinRateCorpusStore


class TestOntokinRateCorpus(unittest.TestCase):
    def test_upsert_and_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "rate.sqlite")
            store = OntokinRateCorpusStore(db)
            try:
                store.upsert_rows(
                    [
                        {
                            "reaction_iri": "http://ex/r1",
                            "equation": "H2 + OH = H2O",
                            "mechanism_iri": "http://ex/m1",
                            "model_kind": "arrhenius",
                            "submodel_iri": "http://ex/sm1",
                            "arrhenius_a": "1.0E10",
                            "arrhenius_n": "-1.0",
                            "arrhenius_ea": "1000",
                        }
                    ]
                )
                hits = store.search_rate_models(equation_fragment="H2", limit=5)
                self.assertEqual(len(hits), 1)
                self.assertEqual(hits[0]["arrhenius_a"], "1.0E10")
            finally:
                store.close()

    @patch("mini_marie.marie.chemistry.ontokin_rate_corpus.fetch_rate_model_rows_with_retry")
    @patch("mini_marie.marie.chemistry.ontokin_rate_corpus.count_reactions_with_kinetic_remote")
    def test_warm_batch_advances(self, mock_count, mock_fetch):
        mock_count.return_value = 10
        mock_fetch.return_value = (
            [
                {
                    "reaction_iri": "http://ex/r1",
                    "equation": "A = B",
                    "mechanism_iri": "http://ex/m1",
                    "model_kind": "arrhenius",
                    "submodel_iri": "http://ex/sm1",
                    "arrhenius_a": "1",
                    "arrhenius_n": "0",
                    "arrhenius_ea": "0",
                }
            ],
            ["http://ex/r1"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "rate.sqlite")
            store = OntokinRateCorpusStore(db)
            try:
                batch = store.warm_batch(batch_size=5, total_reactions=10)
                self.assertEqual(batch.reactions_in_batch, 1)
                state = store.warm_state()
                self.assertEqual(state["offset_next"], 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
