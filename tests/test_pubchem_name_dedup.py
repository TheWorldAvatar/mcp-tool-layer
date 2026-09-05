from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.mcp_servers.pubchem.name_dedup import (
    deterministic_name_filter,
    finalize_pubchem_payload,
    slim_compound_record,
    slim_pubchem_payload,
)


class TestPubchemNameDedup(unittest.TestCase):
    def test_strips_purity_registry_and_loops(self) -> None:
        names = [
            "VOSO4",
            "Vanadyl sulfate (VO(SO4))",
            "Vanadyl sulfate (VO(SO4))",
            "2,2'-Bipyridine-5,5'-dicarboxylic acid, 99%",
            "2,2'-Bipyridine-5,5'-dicarboxylic acid, 99.999%",
            "DTXSID7026318",
            "UNII-SQE0U3LG60",
            "1291-32-3",
            "EINECS 215-066-8",
        ]
        cleaned = deterministic_name_filter(names)
        self.assertEqual(
            cleaned,
            ["VOSO4", "Vanadyl sulfate (VO(SO4))", "2,2'-Bipyridine-5,5'-dicarboxylic acid"],
        )

    def test_slim_record_drops_physchem_and_raw_synonyms(self) -> None:
        record = {
            "cid": 1291,
            "iupac_name": "zirconocene dichloride",
            "molecular_formula": "C10H10Cl2Zr",
            "xlogp": 1.2,
            "tpsa": 0,
            "synonyms": [
                "Cp2ZrCl2",
                "DTXSID7026318",
                "zirconocene dichloride, 99%",
            ],
        }
        slim = slim_compound_record(record, query="Cp2ZrCl2", use_llm=False)
        self.assertEqual(slim["cid"], 1291)
        self.assertEqual(slim["formula"], "C10H10Cl2Zr")
        self.assertNotIn("xlogp", slim)
        self.assertNotIn("synonyms", slim)
        self.assertEqual(slim["names"], ["Cp2ZrCl2", "zirconocene dichloride"])

    def test_keeps_all_unique_names_without_a_count_cap(self) -> None:
        names = [f"Alias {index}" for index in range(20)]
        record = {"cid": 1, "molecular_formula": "H2O", "synonyms": names}
        slim = slim_compound_record(record, query="water", use_llm=False)
        self.assertEqual(slim["names"][0], "water")
        self.assertEqual(len(slim["names"]), 21)

    def test_llm_dedup_does_not_slice_the_returned_list(self) -> None:
        names = [f"Alias {index}" for index in range(12)]
        record = {"cid": 1, "molecular_formula": "H2O", "synonyms": names}
        kept = ["water"] + names

        with TemporaryDirectory() as tmp:
            with patch(
                "src.mcp_servers.pubchem.name_dedup._cache_dir",
                return_value=Path(tmp),
            ), patch(
                "src.mcp_servers.pubchem.name_dedup._invoke_name_dedup",
                return_value=kept,
            ) as mocked:
                slim = slim_compound_record(record, query="water", use_llm=True)
        mocked.assert_called_once()
        self.assertEqual(slim["names"], kept)

    def test_payload_list_and_error_passthrough(self) -> None:
        payload = slim_pubchem_payload(
            [{"error": "missing"}, {"cid": 2, "iupac_name": "methanol", "synonyms": []}],
            query="methanol",
            use_llm=False,
        )
        self.assertEqual(payload[0], {"error": "missing"})
        self.assertEqual(payload[1]["names"], ["methanol"])

    def test_empty_compound_list_is_explicit_miss_not_blank_success(self) -> None:
        result = finalize_pubchem_payload(
            [],
            query="2-amino-1,4-benzenedicarboxylate",
        )
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["ok"])
        self.assertFalse(result[0]["matched"])
        self.assertIn("2-amino-1,4-benzenedicarboxylate", result[0]["error"])
        self.assertIn("unresolved", result[0]["instruction"].lower())
        self.assertNotEqual(result, [])

    def test_empty_dict_and_none_are_explicit_misses(self) -> None:
        for payload in (None, {}):
            result = finalize_pubchem_payload(payload, query="VOSO4")
            self.assertFalse(result[0]["ok"])
            self.assertEqual(result[0]["query"], "VOSO4")

    def test_nonempty_hit_is_unchanged_by_finalize(self) -> None:
        result = finalize_pubchem_payload(
            {"cid": 34007, "iupac_name": "oxovanadium(2+) sulfate", "synonyms": ["VOSO4"]},
            query="VOSO4",
            use_llm=False,
        )
        self.assertEqual(result["cid"], 34007)
        self.assertIn("VOSO4", result["names"])


if __name__ == "__main__":
    unittest.main()
