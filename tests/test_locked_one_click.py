"""Tests for eval zip helpers and the locked one-click CLI."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from models.locations import repository_root
from src.extraction_runtime.cli import build_parser as extraction_parser

SCRIPTS = repository_root() / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from eval_inputs import (  # noqa: E402
    CHEMISTRY_PACKS,
    MEDICAL_PACK,
    _safe_zip_member,
    chemistry_pack_path,
    expected_pdf_relpaths,
    mcp_pack_ready,
    pack_mcp,
    pack_pdfs,
    unpack_zip,
    write_expected_manifest,
)
from run_locked import LOCKED_PROTOCOLS, build_parser as locked_parser, resolve_models  # noqa: E402
from ship_lib import pdf_stem, select_eval_cases  # noqa: E402


class EvalInputTests(unittest.TestCase):
    def test_expected_pdf_names_follow_paper_lists(self) -> None:
        names = expected_pdf_relpaths()
        main = select_eval_cases(1, kind="main")[0]
        medical = select_eval_cases(1, kind="ontomed")[0]
        self.assertIn(
            f"scenarios/mops/datasets/eval30/{pdf_stem(main['doi'])}.pdf",
            names["chemistry"],
        )
        self.assertIn(
            f"scenarios/medical/datasets/eval30/{pdf_stem(medical['doi'])}.pdf",
            names["ontomed"],
        )
        self.assertEqual(len(names["chemistry"]), 60)
        self.assertEqual(len(names["ontomed"]), 30)

    def test_pack_ids(self) -> None:
        self.assertTrue(str(chemistry_pack_path("s1")).endswith("0908-fullpack-s1_newmcp"))
        self.assertTrue(str(CHEMISTRY_PACKS["s4"]).endswith("0908-fullpack-kimi_newmcp"))
        self.assertTrue(str(MEDICAL_PACK).endswith("med-s1_newmcp"))
        with self.assertRaises(ValueError):
            chemistry_pack_path("s9")

    def test_zip_slip_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _safe_zip_member("../secret.txt")
        with self.assertRaises(ValueError):
            _safe_zip_member("/etc/passwd")

    def test_pack_unpack_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            pdf_dir = root / "scenarios" / "mops" / "datasets" / "eval30"
            pdf_dir.mkdir(parents=True)
            sample = pdf_dir / "10.1002_anie.201811027.pdf"
            sample.write_bytes(b"%PDF-fake")
            mcp = root / "generated" / "runs" / "0908-fullpack-s1_newmcp" / "scripts"
            mcp.mkdir(parents=True)
            (mcp / "ontosynthesis").mkdir()
            (mcp / "ontosynthesis" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            pdf_zip = pack_pdfs(root=root, force=True)
            mcp_zip = pack_mcp(root=root, force=True)
            self.assertTrue(pdf_zip.is_file())
            self.assertTrue(mcp_zip.is_file())
            dest = root / "out"
            dest.mkdir()
            unpack_zip(pdf_zip, root=dest, force=True)
            unpack_zip(mcp_zip, root=dest, force=True)
            self.assertTrue((dest / sample.relative_to(root)).is_file())
            self.assertTrue(
                (dest / "generated/runs/0908-fullpack-s1_newmcp/scripts/ontosynthesis/main.py").is_file()
            )

    def test_expected_manifest_lists_zip_names(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "src" / "kg_building" / "ontologx").mkdir(parents=True)
            papers = {"papers": [{"hash": "a014d993", "doi": "10.1002/anie.201811027"}]}
            med = {"papers": [{"hash": "23a00605", "doi": "10062026 OPR10a"}]}
            (root / "src/kg_building/ontologx/papers_eval30.json").write_text(
                json.dumps(papers), encoding="utf-8"
            )
            (root / "src/kg_building/ontologx/papers_medical.json").write_text(
                json.dumps(med), encoding="utf-8"
            )
            path = write_expected_manifest(root=root)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["pdf_zip"], "eval30_pdfs.zip")
            self.assertEqual(payload["mcp_zip"], "locked_mcp_packs.zip")


class LockedRunnerTests(unittest.TestCase):
    def test_parser_defaults(self) -> None:
        args = locked_parser().parse_args([])
        self.assertEqual(args.protocol, "generic-strict")
        self.assertEqual(args.builder, "both")
        self.assertEqual(args.pack, "s1")
        self.assertEqual(args.cases, 1)
        self.assertEqual(set(LOCKED_PROTOCOLS), {"generic-strict", "generic-noprompt", "with-prompt"})

    def test_model_aliases(self) -> None:
        args = locked_parser().parse_args(["--kg-model", "kimi", "--extract-model", "gpt-4.1"])
        kg, extract = resolve_models(args)
        self.assertEqual(kg, "moonshotai/kimi-k3")
        self.assertEqual(extract, "gpt-4.1-2025-04-14")

    def test_list_and_dry_run_exit_zero(self) -> None:
        from run_locked import main

        self.assertEqual(main(["--list"]), 0)
        self.assertEqual(main(["--dry-run", "--domain", "main", "--builder", "pipeline"]), 0)

    def test_extraction_cli_accepts_model_overrides(self) -> None:
        args = extraction_parser().parse_args(
            [
                "ontosynthesis",
                "--protocol",
                "generic-strict",
                "--kg-model",
                "moonshotai/kimi-k3",
                "--extraction-model",
                "gpt-4.1-2025-04-14",
            ]
        )
        self.assertEqual(args.kg_model, "moonshotai/kimi-k3")
        self.assertEqual(args.extraction_model, "gpt-4.1-2025-04-14")

    def test_mcp_ready_requires_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw)
            self.assertFalse(mcp_pack_ready(path))
            (path / "scripts").mkdir()
            self.assertTrue(mcp_pack_ready(path))


if __name__ == "__main__":
    unittest.main()
