"""Unit tests for the shipped Windows pipeline helpers."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from models.locations import repository_root

SCRIPTS = repository_root() / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ship_lib import (  # noqa: E402
    DEFAULT_WORKERS,
    MEDICAL_GOLD,
    MEDICAL_SCHEMA,
    bounded_workers,
    chemistry_score_table,
    find_scorer_repo,
    format_score_report,
    missing_pdfs,
    parse_fine_grained_f1,
    parse_step,
    pdf_stem,
    scorer_looks_valid,
    select_eval_cases,
)


FIRST_TEN_MAIN = [
    "a014d993",
    "50307a45",
    "1b9180ec",
    "7ba809dd",
    "88c21a74",
    "9e93418f",
    "d5ff239e",
    "dc2e2fef",
    "3a4646d4",
    "c66a0a79",
]

FIRST_TEN_ONTOMED = [
    "23a00605",
    "7db20b59",
    "4beb9c08",
    "36f46a9f",
    "7ba939bd",
    "d626d799",
    "55aa99e4",
    "5cc68f8c",
    "6cc343f0",
    "e7734aa9",
]


class ShipLibTests(unittest.TestCase):
    def test_selects_official_eval_order(self) -> None:
        main = select_eval_cases(10, kind="main")
        medical = select_eval_cases(10, kind="ontomed")
        self.assertEqual([item["hash"] for item in main], FIRST_TEN_MAIN)
        self.assertEqual([item["hash"] for item in medical], FIRST_TEN_ONTOMED)
        self.assertEqual(pdf_stem(main[0]["doi"]), "10.1002_anie.201811027")

    def test_case_bounds(self) -> None:
        self.assertEqual(len(select_eval_cases(1, kind="main")), 1)
        self.assertEqual(len(select_eval_cases(30, kind="ontomed")), 30)
        with self.assertRaises(ValueError):
            select_eval_cases(0, kind="main")
        with self.assertRaises(ValueError):
            select_eval_cases(31, kind="ontomed")

    def test_parse_step_names(self) -> None:
        self.assertEqual(parse_step("extract"), 3)
        self.assertEqual(parse_step(5), 5)
        with self.assertRaises(ValueError):
            parse_step("0")

    def test_bounded_workers(self) -> None:
        self.assertEqual(bounded_workers(5, 10), 5)
        self.assertEqual(bounded_workers(5, 2), 2)
        self.assertEqual(bounded_workers(0, 10), 1)
        self.assertEqual(DEFAULT_WORKERS, 5)

    def test_parse_fine_grained_f1(self) -> None:
        markdown = (
            "**Fine-grained Scoring:** TP=1 FP=2 FN=3 | P=0.100 R=0.200 F1=0.123\n"
            "**Fine-grained Scoring:** TP=10 FP=1 FN=1 | P=0.800 R=0.900 F1=0.847\n"
        )
        self.assertEqual(parse_fine_grained_f1(markdown), 0.847)
        self.assertEqual(
            parse_fine_grained_f1("**Current Overall**: P=0.928 R=0.863 F1=0.894\n"),
            0.894,
        )
        self.assertEqual(
            parse_fine_grained_f1(
                "**Combined Scoring Summary:** TP=0 FP=0 FN=8 | P=0.000 R=0.000 F1=0.000\n"
            ),
            0.000,
        )
        self.assertIsNone(parse_fine_grained_f1("no scores"))

    def test_chemistry_table_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            scores = Path(raw)
            steps = scores / "scoring_steps"
            steps.mkdir(parents=True)
            (steps / "_overall.md").write_text(
                "**Fine-grained Scoring:** TP=1 FP=0 FN=0 | P=1.000 R=1.000 F1=0.910\n",
                encoding="utf-8",
            )
            table = chemistry_score_table(scores)
            self.assertEqual(table["scoring_steps"], 0.91)
            self.assertIsNone(table["scoring_cbu"])
            report = format_score_report(
                cases=10,
                main_scores=table,
                medical={"overall_accuracy": 0.8, "mean_per_case_accuracy": 0.75, "n_cases": 10},
            )
            self.assertIn("F1=0.910", report)
            self.assertIn("80.0%", report)
            self.assertIn("generic-strict", report)

    def test_missing_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / "10.1002_anie.201811027.pdf").write_bytes(b"%PDF")
            missing = missing_pdfs(
                [
                    {"hash": "a014d993", "doi": "10.1002/anie.201811027"},
                    {"hash": "50307a45", "doi": "10.1002/chem.201604264"},
                ],
                folder,
            )
            self.assertEqual(missing, ["50307a45 (10.1002_chem.201604264.pdf)"])

    def test_find_scorer_repo(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fake = Path(raw) / "scorer"
            for relative in (
                "evaluation/scoring_steps.py",
                "scripts/merge_and_conversion_main.py",
                "scripts/medical_ttl_to_csv_sparql.py",
                "scripts/medical_score_predicted_vs_gold.py",
            ):
                path = fake / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("placeholder\n", encoding="utf-8")
            self.assertTrue(scorer_looks_valid(fake))
            found = find_scorer_repo(fake)
            self.assertEqual(found, fake.resolve())

    def test_medical_gold_lives_in_this_repo(self) -> None:
        root = repository_root()
        self.assertTrue((root / MEDICAL_GOLD).is_file())
        self.assertTrue((root / MEDICAL_SCHEMA).is_file())
        self.assertTrue((root / "data" / "scorer_assets" / "full_ground_truth" / "steps").is_dir())

    def test_overlay_copies_missing_ground_truth(self) -> None:
        from src.kg_building.scorer_repo import overlay_scorer_assets

        with tempfile.TemporaryDirectory() as raw:
            scorer = Path(raw) / "engines"
            scorer.mkdir()
            overlay_scorer_assets(scorer)
            self.assertTrue((scorer / "full_ground_truth" / "steps").is_dir())
            self.assertTrue((scorer / MEDICAL_GOLD).is_file())
            self.assertTrue((scorer / MEDICAL_SCHEMA).is_file())

    def test_steps_gold_has_no_vessel_fields(self) -> None:
        from src.kg_building.scorer_protocol import json_has_vessel_keys

        steps = repository_root() / "data" / "scorer_assets" / "full_ground_truth" / "steps"
        self.assertTrue(steps.is_dir())
        for path in sorted(steps.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(json_has_vessel_keys(payload), msg=path.name)

    def test_overlay_replaces_old_ground_truth(self) -> None:
        from src.kg_building.scorer_repo import overlay_scorer_assets

        with tempfile.TemporaryDirectory() as raw:
            scorer = Path(raw) / "engines"
            stale = scorer / "full_ground_truth" / "steps"
            stale.mkdir(parents=True)
            (stale / "stale.json").write_text(
                '{"Synthesis":[{"steps":[{"Add":{"usedVesselName":"flask"}}]}]}',
                encoding="utf-8",
            )
            overlay_scorer_assets(scorer)
            self.assertFalse((stale / "stale.json").is_file())
            sample = next((scorer / "full_ground_truth" / "steps").glob("*.json"))
            text = sample.read_text(encoding="utf-8")
            self.assertNotIn("usedVesselName", text)

    def test_lock_cloned_steps_engine(self) -> None:
        from src.kg_building.scorer_protocol import lock_cloned_steps_engine

        with tempfile.TemporaryDirectory() as raw:
            scorer = Path(raw) / "engines"
            path = scorer / "evaluation" / "scoring_steps.py"
            path.parent.mkdir(parents=True)
            path.write_text(
                'VESSEL_FIELDS = {\n'
                '    "usedVesselName",\n'
                '    "usedVesselType",\n'
                '    "targetVesselName",\n'
                '    "targetVesselType",\n'
                '}\n'
                '        if ignore_vessel and key in vessel_fields:\n'
                '            continue\n'
                '            if ignore_vessel and k in vessel_fields:\n'
                '                continue\n'
                '        evaluate_previous(use_anchored=not args.no_anchor, ignore_vessel=args.no_vessel, short_mode=args.short, skip_order=args.skip_order, ignore_mode=args.ignore, use_new_gt=args.new, use_full_gt=args.full, equivalence_config=equivalence_config)\n'
                '        evaluate_current(ignore_vessel=args.no_vessel, short_mode=args.short, skip_order=args.skip_order, ignore_mode=args.ignore, use_new_gt=args.new, use_full_gt=args.full, equivalence_config=equivalence_config, hash_filter=set(args.hashes or []), correct_ccdc_by_name=args.correct_ccdc_by_name, pred_root=args.pred_root, out_root=args.out_root)\n',
                encoding="utf-8",
            )
            lock_cloned_steps_engine(scorer)
            text = path.read_text(encoding="utf-8")
            self.assertIn("TWA_LOCKED_STEPS_PROTOCOL", text)
            self.assertIn("skip_order=True", text)
            self.assertIn("ignore_vessel=False", text)
            self.assertIn('"sealedVessel"', text)
            self.assertNotIn("if ignore_vessel and key in vessel_fields", text)
            lock_cloned_steps_engine(scorer)
            self.assertEqual(text.count("TWA_LOCKED_STEPS_PROTOCOL"), 1)


class ShipCliTests(unittest.TestCase):
    def test_help_and_dry_run_parse(self) -> None:
        from run_default_pipeline import build_parser

        args = build_parser().parse_args(["10", "--from-step", "score", "--dry-run"])
        self.assertEqual(args.cases_positional, 10)
        self.assertEqual(args.from_step, "score")
        self.assertTrue(args.dry_run)
        self.assertEqual(args.workers, 5)
        args = build_parser().parse_args(["--workers", "3"])
        self.assertEqual(args.workers, 3)


if __name__ == "__main__":
    unittest.main()
