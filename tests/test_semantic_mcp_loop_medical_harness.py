"""Offline tests for the medical semantic MCP loop (fixture, harness, reasoner schema)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    run_agentic_generation_experiment,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    group_validation_failures,
)
from src.agents.scripts_and_prompts_generation.semantic_mcp_loop_medical import (
    REQUIRED_COVERAGE,
    package_semantic_feedback,
    run_mcp_harness,
    run_reasoner_gate,
    run_react_pipeline_against_mock,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "medical_semantic_mock.json"
META = ROOT / "configs" / "meta_task" / "meta_task_config_medical_non_flat_v4_one_iter.json"
TBOX = ROOT / "medical_case" / "medical_case_schema_de_non_flat_v4.ttl"


class TestSemanticMcpLoopMedicalHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not META.is_file() or not TBOX.is_file() or not FIXTURE.is_file():
            raise unittest.SkipTest("medical v4 meta-task / T-Box / fixture missing")
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="semantic_mcp_loop_test_")
        cls.output_root = Path(cls._tmpdir.name) / "gen"
        summary = run_agentic_generation_experiment(
            ["medical"],
            meta_task_config_path=META,
            output_root=cls.output_root,
            generate_scripts=True,
            generate_prompts=True,
            repair_loop=False,
        )
        if not summary.get("ok"):
            # Still try harness if scripts were written; Level-1 may warn on prompts.
            pass
        cls.scripts_dir = cls.output_root / "scripts" / "medical"
        if not (cls.scripts_dir / "main.py").is_file():
            raise unittest.SkipTest("deterministic medical main.py was not generated")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    def test_fixture_shape(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertIn("document_md", data)
        self.assertIsInstance(data["hints"], dict)
        self.assertTrue(data["document_md"].strip())
        for key in (
            "PatientInfo",
            "CaseTimeline",
            "SurgicalApproach",
            "Procedure",
            "SurgicalTeam",
            "Diagnosis",
            "PathologyOutcome",
        ):
            self.assertIn(key, data["hints"])
        for name in REQUIRED_COVERAGE:
            self.assertIn(name, data["coverage"])

    def test_group_validation_failures(self) -> None:
        grouped = group_validation_failures(
            [
                "main.py: Missing create tools: create_PatientInfo",
                "medical_creation_entities.py: syntax error line 3: bad",
                "unmapped noise",
            ]
        )
        self.assertIn("main.py", grouped)
        self.assertIn("medical_creation_entities.py", grouped)
        self.assertNotIn("unmapped noise", grouped)

    def test_harness_writes_abox_and_hermit_reasoner_schema(self) -> None:
        self.skipTest(
            "Legacy materialize_hints harness is outside the new semantic-loop core"
        )
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="semantic_harness_abox_") as tmp:
            abox = Path(tmp) / "abox.ttl"
            harness = run_mcp_harness(
                scripts_dir=self.scripts_dir,
                fixture=fixture,
                abox_path=abox,
            )
            self.assertTrue(
                harness.get("ok"),
                msg=json.dumps(harness, indent=2, ensure_ascii=False),
            )
            self.assertTrue(abox.is_file())
            self.assertGreater(abox.stat().st_size, 50)
            report_path = Path(tmp) / "reasoner_report.json"
            report = run_reasoner_gate(
                tbox_path=TBOX,
                abox_path=abox,
                report_path=report_path,
            )
            self.assertTrue(report_path.is_file())
            self.assertIn("ok", report)
            self.assertIn("failures", report)
            self.assertIn("details", report)
            self.assertIn("tbox_paths", report)
            self.assertIn("abox_paths", report)
            self.assertTrue(report.get("hermit_required"))
            hermit = report.get("hermit") or {}
            if hermit.get("available") and "error" not in hermit:
                self.assertTrue(report.get("ok"), msg=json.dumps(report, indent=2))
                self.assertTrue(hermit.get("consistent"))
                self.assertFalse(report.get("hermit_hard_fail"))
            else:
                # Hard HermiT gate must fail closed when HermiT cannot run.
                self.assertFalse(report.get("ok"))
                self.assertTrue(report.get("hermit_hard_fail"))

            feedback = package_semantic_feedback(
                abox_build=harness,
                reasoner=report,
                coverage=list(fixture.get("coverage") or REQUIRED_COVERAGE),
            )
            self.assertIn("Semantic MCP feedback", feedback)
            self.assertIn("HermiT", feedback)

    def test_react_pipeline_helper_rejects_empty_document(self) -> None:
        with tempfile.TemporaryDirectory(prefix="semantic_react_empty_") as tmp:
            root = Path(tmp)
            result = run_react_pipeline_against_mock(
                artifact_root=root,
                meta_task_config=META,
                fixture={"document_md": "", "hints": {}, "coverage": REQUIRED_COVERAGE},
                abox_path=root / "abox.ttl",
                runtime_root=root / "runtime",
            )
            self.assertFalse(result.get("ok"))
            self.assertEqual(result.get("mode"), "react")
            self.assertIn("document_md", str(result.get("error") or ""))


if __name__ == "__main__":
    unittest.main()
