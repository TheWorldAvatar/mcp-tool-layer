"""Tests for chemistry SQLite cache."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from mini_marie.cache_tiers import TIER_FULL, TIER_PROBE
from mini_marie.marie.chemistry.chemistry_cache import (
    ChemistryCache,
    _canonical_args,
    invoke_tool,
    warm_full_calls,
)
from mini_marie.marie.chemistry.limits import DEFAULT_ONLINE_PROBE_LIMIT
from mini_marie.marie.chemistry.warm_option_catalog import (
    coverage_report,
    full_space_warm_specs,
    slice_specs,
)


class TestWarmOptionCatalog(unittest.TestCase):
    def test_full_space_expands_vary_dimension(self):
        specs = full_space_warm_specs(dimension_id="ontokin_reaction_fragment")
        fragments = {s["args"]["reaction_fragment"] for s in specs}
        self.assertIn("H2O2", fragments)
        self.assertGreaterEqual(len(specs), 5)

    def test_coverage_tracks_missing_options(self):
        def has_full(tool: str, args: dict) -> bool:
            return args.get("reaction_fragment") == "H2O2"

        report = coverage_report(
            has_full=has_full,
            dimension_id="ontokin_reaction_fragment",
            missing_limit=5,
        )
        dim = report["dimensions"][0]
        self.assertGreater(dim["total"], 1)
        self.assertEqual(dim["cached"], 1)
        self.assertEqual(dim["missing"], dim["total"] - 1)

    def test_slice_specs_batch(self):
        specs = [{"tool": "t", "args": {"i": i}} for i in range(10)]
        batch = slice_specs(specs, offset=2, batch=3)
        self.assertEqual(len(batch), 3)
        self.assertEqual(batch[0]["args"]["i"], 2)


class TestChemistryCache(unittest.TestCase):
    def test_cache_key_stable(self):
        from mini_marie.cache_tiers import make_cache_key

        args = {"namespace": "ontokin", "class_local": "ReactionMechanism"}
        k1 = make_cache_key("lookup_individuals", _canonical_args(args), TIER_PROBE, probe_limit=5)
        k2 = make_cache_key("lookup_individuals", _canonical_args(args), TIER_PROBE, probe_limit=5)
        self.assertEqual(k1, k2)

    @patch("mini_marie.marie.chemistry.chemistry_cache._dispatch_tool")
    def test_invoke_online_stores_probe_tier(self, mock_dispatch):
        mock_dispatch.return_value = [{"subject": "s1"}]
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.sqlite")
            cache = ChemistryCache(db)
            try:
                rows, meta = invoke_tool(
                    "lookup_individuals",
                    {"namespace": "ontokin", "class_local": "ReactionMechanism"},
                    mode="online",
                    cache=cache,
                )
                self.assertEqual(len(rows), 1)
                self.assertFalse(meta["from_cache"])
                self.assertTrue(
                    cache.has_tier(
                        "lookup_individuals",
                        {"namespace": "ontokin", "class_local": "ReactionMechanism"},
                        TIER_PROBE,
                    )
                )
            finally:
                cache.close()

    @patch("mini_marie.marie.chemistry.chemistry_cache._dispatch_tool")
    def test_offline_cache_miss(self, mock_dispatch):
        mock_dispatch.return_value = [{"count": "1"}]
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "test.sqlite")
            cache = ChemistryCache(db)
            try:
                from mini_marie.cache_tiers import CacheMissError

                with self.assertRaises(CacheMissError):
                    invoke_tool(
                        "count_instances",
                        {"namespace": "ontokin", "class_local": "GasPhaseReaction"},
                        mode="offline",
                        cache=cache,
                    )
            finally:
                cache.close()


@unittest.skipUnless(os.environ.get("CHEMISTRY_WARM") == "1", "Set CHEMISTRY_WARM=1 for live warm test")
class TestLiveWarm(unittest.TestCase):
    def test_warm_single_spec(self):
        specs = [
            {
                "tool": "count_instances",
                "args": {"namespace": "ontoprovenance", "class_local": "Person"},
            }
        ]
        summary = warm_full_calls(specs, delay_seconds=1.0)
        self.assertEqual(len(summary["errors"]), 0)


if __name__ == "__main__":
    unittest.main()
