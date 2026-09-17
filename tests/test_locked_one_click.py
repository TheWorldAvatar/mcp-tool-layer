"""Tests for eval zip helpers and the locked one-click CLI."""

from __future__ import annotations

import argparse
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
        self.assertEqual(args.protocol, "minimal")
        self.assertEqual(args.builder, "both")
        self.assertEqual(args.pack, "s1")
        self.assertEqual(args.cases, 1)
        self.assertEqual(set(LOCKED_PROTOCOLS), {"minimal", "graph-rules", "kg-guidance"})

    def test_guidance_aliases(self) -> None:
        from run_locked import guidance_line, normalize_guidance

        self.assertEqual(normalize_guidance("Minimal"), "minimal")
        self.assertEqual(normalize_guidance("graph rules"), "graph-rules")
        self.assertEqual(normalize_guidance("KG guidance"), "kg-guidance")
        self.assertEqual(normalize_guidance("generic-strict"), "minimal")
        self.assertEqual(normalize_guidance("generic-noprompt"), "graph-rules")
        self.assertEqual(normalize_guidance("with-prompt"), "kg-guidance")
        self.assertIn("occurrence and ownership", guidance_line("minimal"))
        self.assertIn("T-Box handbook", guidance_line("graph-rules"))
        self.assertIn("human-engineered", guidance_line("kg-guidance"))
        args = locked_parser().parse_args(["--protocol", "graph-rules"])
        self.assertEqual(args.protocol, "graph-rules")

    def test_model_aliases(self) -> None:
        args = locked_parser().parse_args(["--kg-model", "kimi", "--extract-model", "gpt-4.1"])
        kg, extract = resolve_models(args)
        self.assertEqual(kg, "moonshotai/kimi-k3")
        self.assertEqual(extract, "gpt-4.1-2025-04-14")

    def test_list_and_dry_run_exit_zero(self) -> None:
        from run_locked import main

        self.assertEqual(main(["--list"]), 0)
        self.assertEqual(main(["--dry-run", "--domain", "main", "--builder", "pipeline"]), 0)

    def test_hash_flag_overrides_case_slice(self) -> None:
        args = locked_parser().parse_args(["--hash", "a014d993", "--hash", "88c21a74"])
        self.assertEqual(args.hashes, ["a014d993", "88c21a74"])

    def test_ox_uses_official_s1_letter_jobs(self) -> None:
        from run_locked import (
            OX_GROUP_SIZE,
            S1_LETTER_GROUPS,
            official_letter_groups,
            ox_letter_groups,
        )

        self.assertEqual(OX_GROUP_SIZE, 2)
        self.assertEqual(S1_LETTER_GROUPS["a"], ("0c57bac8", "7ba809dd"))
        self.assertEqual(S1_LETTER_GROUPS["b"], ("d5ff239e", "a014d993"))
        self.assertEqual(S1_LETTER_GROUPS["i"], ("7fa3bf7d", "88c21a74"))
        self.assertEqual(len(S1_LETTER_GROUPS), 15)
        all_hashes = [item for pair in S1_LETTER_GROUPS.values() for item in pair]
        self.assertEqual(len(all_hashes), 30)
        self.assertEqual(len(set(all_hashes)), 30)
        groups = official_letter_groups(all_hashes)
        self.assertEqual(len(groups), 15)
        self.assertTrue(all(len(group) == 2 for _letter, group in groups))
        five = official_letter_groups(
            ["a014d993", "50307a45", "1b9180ec", "7ba809dd", "88c21a74"]
        )
        self.assertEqual({letter for letter, _group in five}, {"a", "b", "c", "d", "i"})
        self.assertTrue(all(len(group) == 1 for _letter, group in five))
        self.assertEqual(
            ox_letter_groups(["m1", "m2", "m3"], chemistry=False),
            [("a", ["m1", "m2"]), ("b", ["m3"])],
        )

    def test_locked_jobs_are_one_paper_per_process(self) -> None:
        from run_locked import EXTRACT_GROUP_SIZE, KG_GROUP_SIZE, group_suffix, hash_groups

        self.assertEqual(EXTRACT_GROUP_SIZE, 1)
        self.assertEqual(KG_GROUP_SIZE, 1)
        self.assertEqual(
            hash_groups(
                ["a014d993", "50307a45", "1b9180ec", "7ba809dd", "88c21a74"],
                EXTRACT_GROUP_SIZE,
            ),
            [
                ["a014d993"],
                ["50307a45"],
                ["1b9180ec"],
                ["7ba809dd"],
                ["88c21a74"],
            ],
        )
        self.assertEqual("".join(group_suffix(i) for i in range(15)), "abcdefghijklmno")
        self.assertEqual(group_suffix(26), "aa")

    def test_kg_run_config_is_s1ka_shape(self) -> None:
        from run_locked import _prepare_kg_run_config

        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw)
            (run_dir / "runtime").mkdir()
            config_path = run_dir / "pipeline.resolved.json"
            config_path.write_text(
                json.dumps(
                    {
                        "ontology": "ontosynthesis",
                        "steps": [
                            "pdf_conversion",
                            "tbox_slim",
                            "top_entity_extraction",
                            "top_entity_kg_building",
                            "main_ontology_extractions",
                        ],
                    }
                ),
                encoding="utf-8",
            )
            _prepare_kg_run_config(
                run_dir, engine="generic-strict", kg_model="openai/gpt-4o-2024-11-20"
            )
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["steps"], ["main_kg_building"])
            self.assertEqual(payload["experiment_protocol"], "generic-strict")
            self.assertEqual(payload["pipeline_kg"], "no-contract")
            self.assertEqual(payload["kg_model"], "openai/gpt-4o-2024-11-20")
        from run_locked import _pipeline_argv

        argv = _pipeline_argv(
            ontology="ontosynthesis",
            pack_root=Path("pack"),
            engine="generic-strict",
            tag="lks1min",
            hashes=["a014d993"],
            workers=1,
            kg_model="openai/gpt-4o-2024-11-20",
            extract_model=None,
            config_path=Path("scenarios/mops/runs/lks1min/pipeline.resolved.json"),
            score=False,
        )
        self.assertNotIn("--protocol", argv)
        self.assertEqual(argv[argv.index("--workers") + 1], "1")
        self.assertEqual(argv.count("--hash"), 1)
        self.assertIn("--resume", argv)
        self.assertIn("--test", argv)
        self.assertIn("--config", argv)

    def test_locked_runtime_pins_match_official_mcp_layer(self) -> None:
        from ship_lib import LOCKED_PYTHON, LOCKED_RUNTIME_VERSIONS, locked_runtime_mismatches

        self.assertEqual(LOCKED_PYTHON, (3, 11))
        self.assertEqual(LOCKED_RUNTIME_VERSIONS["mcp"], "1.10.1")
        self.assertEqual(LOCKED_RUNTIME_VERSIONS["fastmcp"], "2.10.1")
        self.assertEqual(LOCKED_RUNTIME_VERSIONS["langchain"], "0.3.26")
        self.assertEqual(LOCKED_RUNTIME_VERSIONS["langchain-mcp-adapters"], "0.1.7")
        self.assertEqual(LOCKED_RUNTIME_VERSIONS["langgraph"], "0.4.8")
        self.assertEqual(LOCKED_RUNTIME_VERSIONS["anyio"], "4.9.0")
        self.assertEqual(LOCKED_RUNTIME_VERSIONS["openai"], "1.91.0")
        import sys

        if sys.version_info[:2] != (3, 11):
            self.assertTrue(locked_runtime_mismatches())

    def test_incomplete_kg_run_is_resumed_not_copied(self) -> None:
        from run_locked import _copy_extract

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            extract = root / "extract"
            existing = root / "kg"
            extract.mkdir()
            existing.mkdir()
            (existing / "pipeline.resolved.json").write_text("{}", encoding="utf-8")
            kg_run = existing
            if kg_run is None or not (kg_run / "pipeline.resolved.json").is_file():
                kg_run = _copy_extract(extract, "mops", "lks1min", ["a014d993"])
            self.assertEqual(kg_run, existing)

    def test_spawnable_kg_config_drops_domain(self) -> None:
        from src.kg_building.pipeline.main_kg.run import _spawnable_config

        payload = _spawnable_config(
            {"domain": object(), "ontology_name": "ontosynthesis", "kg_model": "x"}
        )
        self.assertNotIn("domain", payload)
        self.assertEqual(payload["ontology_name"], "ontosynthesis")

    def test_extract_argv_workers_are_always_one(self) -> None:
        from run_locked import EXTRACT_GROUP_SIZE, _extract_argv, hash_groups

        group = hash_groups(["h1", "h2", "h3"], EXTRACT_GROUP_SIZE)[0]
        argv = _extract_argv(
            ontology="ontosynthesis",
            pack_root=Path("pack"),
            tag="lkexs1a",
            hashes=group,
            workers=1,
            extract_model=None,
            config_path=None,
        )
        self.assertEqual(argv[argv.index("--workers") + 1], "1")
        self.assertEqual(argv.count("--hash"), 1)
        self.assertIn("lkexs1a", argv)
        self.assertEqual(argv[argv.index("--generation-run") + 1], "pack")
        self.assertEqual(argv[argv.index("--until") + 1], "main_ontology_extractions")
        self.assertIn("--test", argv)
        self.assertNotIn("src.extraction_prompt_generation", argv)

    def test_extract_argv_never_regenerates_prompts(self) -> None:
        from run_locked import _extract_argv

        argv = _extract_argv(
            ontology="ontosynthesis",
            pack_root=chemistry_pack_path("s1"),
            tag="lkexs1",
            hashes=["a014d993"],
            workers=1,
            extract_model=None,
            config_path=None,
        )
        self.assertTrue(
            str(argv[argv.index("--generation-run") + 1]).endswith("0908-fullpack-s1_newmcp")
        )
        joined = " ".join(argv)
        self.assertNotIn("extraction_prompt_generation", joined)

    def test_prompt_generation_jobs_match_s1_s4_all_domains(self) -> None:
        from run_default_pipeline import _prompt_generation_jobs

        both = argparse.Namespace(domain="both", generation_tag="gpt5", workers=5)
        jobs = _prompt_generation_jobs(both)
        self.assertEqual(len(jobs), 1)
        argv = jobs[0]
        self.assertIn("src.extraction_prompt_generation", argv)
        self.assertIn("--all-domains", argv)
        self.assertEqual(argv[argv.index("--stage") + 1], "all")
        self.assertEqual(argv[argv.index("--workers") + 1], "5")
        self.assertEqual(argv[argv.index("--tag") + 1], "gpt5")

        main = argparse.Namespace(domain="main", generation_tag="gpt5", workers=5)
        names = [job[3] for job in _prompt_generation_jobs(main)]
        self.assertEqual(names, ["ontosynthesis", "ontomops", "ontospecies"])

    def test_frozen_s1_extraction_prompts_match_official_pack(self) -> None:
        from eval_inputs import assert_frozen_extraction_prompts

        pack = chemistry_pack_path("s1")
        if not (pack / "prompts" / "ontosynthesis" / "EXTRACTION_ITER_1.md").is_file():
            self.skipTest("frozen s1 pack is not unpacked")
        assert_frozen_extraction_prompts(pack, pack_id="s1")

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
