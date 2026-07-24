"""Tests for chemistry competency MCP query builders."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from mini_marie.marie.chemistry.limits import ONLINE_PROBE_LIMIT
from mini_marie.marie.chemistry import query_builder as qb


class TestQueryLimits(unittest.TestCase):
    @patch("mini_marie.marie.chemistry.query_builder.execute_sparql_get", return_value=[])
    def test_lookup_query_contains_limit(self, _mock):
        qb.lookup_individuals("ontokin", "ReactionMechanism")
        query = _mock.call_args[0][0]
        self.assertIn(f"LIMIT {ONLINE_PROBE_LIMIT}", query)

    @patch("mini_marie.marie.chemistry.query_builder.execute_sparql_get", return_value=[])
    def test_filter_query_contains_limit(self, _mock):
        qb.filter_by_literal("ontospecies", "Species", "hasMolecularFormula", "C6H8O6", "equals")
        query = _mock.call_args[0][0]
        self.assertIn(f"LIMIT {ONLINE_PROBE_LIMIT}", query)
        self.assertIn("os:value ?propVal", query)
        self.assertIn('LCASE("C6H8O6")', query)

    @patch(
        "mini_marie.marie.chemistry.query_builder.execute_sparql_get",
        return_value=[{"subject": "s1", "propVal": "C6H8O6"}],
    )
    def test_formula_filter_returns_tsv(self, _mock):
        tsv = qb.filter_by_literal(
            "ontospecies", "Species", "hasMolecularFormula", "C6H8O6", "equals"
        )
        self.assertNotEqual(tsv, "No results")


@unittest.skipUnless(os.environ.get("CHEMISTRY_LIVE") == "1", "Set CHEMISTRY_LIVE=1 for live probes")
class TestLiveProbes(unittest.TestCase):
    def test_probe_competency_passes(self):
        from mini_marie.marie.chemistry.probe_competency import run_probes

        report = run_probes()
        self.assertEqual(report["failed"], 0)


if __name__ == "__main__":
    unittest.main()
