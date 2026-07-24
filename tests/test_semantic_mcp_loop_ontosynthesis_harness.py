"""Offline tests for the OntoSynthesis main-only semantic MCP repair loop."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    autofix_ruff_on_scripts,
    run_ruff_on_scripts,
)
from src.agents.scripts_and_prompts_generation.semantic_mcp_loop_ontosynthesis import (
    SEMANTIC_POISON_PROP,
    exercise_level1_fail,
    exercise_semantic_fail,
    package_semantic_feedback,
    _primary_ordering_property,
    _tbox_fixture_inventory,
    run_mcp_harness,
    run_prove_repairs,
    run_reasoner_gate,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ontosynthesis_semantic_mock.json"
META = ROOT / "configs" / "meta_task" / "meta_task_config.json"
CANDIDATE_SCRIPTS = (
    ROOT / "ai_generated_contents_candidate" / "scripts" / "ontosynthesis"
)
TBOX = [
    ROOT / "data/ontologies/ontosynthesis.ttl",
    ROOT / "data/ontologies/ontomops-subgraph.ttl",
    ROOT / "data/ontologies/ontospecies-subgraph.ttl",
    ROOT / "data/ontologies/om2.ttl",
]


class TestSemanticMcpLoopOntosynthesisHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not FIXTURE.is_file() or not META.is_file():
            raise unittest.SkipTest("OntoSyn fixture / meta-task missing")
        if not (CANDIDATE_SCRIPTS / "main.py").is_file():
            raise unittest.SkipTest("candidate ontosynthesis scripts missing")
        if not all(path.is_file() for path in TBOX):
            raise unittest.SkipTest("OntoSyn T-Box stack missing")
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="ontosyn_semantic_loop_test_")
        cls.scripts_dir = Path(cls._tmpdir.name) / "scripts" / "ontosynthesis"
        cls.scripts_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(CANDIDATE_SCRIPTS, cls.scripts_dir)
        cls.context = build_agentic_generation_context(
            ontology_name="ontosynthesis",
            meta_task_config_path=META,
            output_root=Path(cls._tmpdir.name) / "ctx",
            write_files=False,
        )
        cls.ordering_property = _primary_ordering_property(cls.context)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    def test_fixture_shape(self) -> None:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertIn("document_md", data)
        self.assertIsInstance(data["hints"], dict)
        self.assertTrue(data["hints"])
        self.assertTrue(data["document_md"].strip())
        self.assertTrue(data.get("coverage"))

    def test_inventory_comes_from_contract_not_literals(self) -> None:
        inventory = _tbox_fixture_inventory(self.context)
        self.assertTrue(inventory["all_class_locals"])
        self.assertTrue(inventory["all_property_locals"])
        self.assertTrue(inventory["top_level_hint_classes"])
        self.assertEqual(
            inventory["primary_ordering_property"], self.ordering_property
        )
        # Orchestrator helpers must not hard-require a fixed UMC-1 subset.
        self.assertGreaterEqual(len(inventory["all_class_locals"]), 10)

    def test_harness_and_hermit_gate(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="ontosyn_harness_") as tmp:
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
            report = run_reasoner_gate(
                tbox_paths=TBOX,
                abox_path=abox,
                report_path=Path(tmp) / "reasoner_report.json",
            )
            self.assertTrue(report.get("hermit_required"))
            hermit = report.get("hermit") or {}
            if hermit.get("available") and "error" not in hermit:
                self.assertTrue(report.get("ok"), msg=json.dumps(report, indent=2))
                self.assertTrue(hermit.get("consistent"))
            else:
                self.assertFalse(report.get("ok"))
                self.assertTrue(report.get("hermit_hard_fail"))
            feedback = package_semantic_feedback(
                abox_build=harness,
                reasoner=report,
                coverage=list(fixture.get("coverage") or []),
                ordering_property=self.ordering_property,
            )
            self.assertIn("HermiT", feedback)
            self.assertIn(SEMANTIC_POISON_PROP, feedback)

    def test_syntax_defect_survives_ruff_autofix(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ontosyn_l1_syntax_") as tmp:
            scripts = Path(tmp) / "scripts"
            shutil.copytree(self.scripts_dir, scripts)
            injected = exercise_level1_fail(scripts, mode="syntax")
            self.assertTrue(injected)
            self.assertFalse(run_ruff_on_scripts(scripts).get("ok"))
            autofix = autofix_ruff_on_scripts(scripts)
            self.assertFalse(
                (autofix.get("recheck") or {}).get("ok"),
                msg="syntax defect must remain after non-LLM ruff autofix",
            )

    def test_semantic_poison_fails_reasoner_gate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ontosyn_inject_") as tmp:
            scripts = Path(tmp) / "scripts"
            shutil.copytree(self.scripts_dir, scripts)
            poisoned = exercise_semantic_fail(
                scripts, ordering_property=self.ordering_property
            )
            self.assertTrue(poisoned)
            fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
            abox = Path(tmp) / "abox.ttl"
            harness = run_mcp_harness(
                scripts_dir=scripts, fixture=fixture, abox_path=abox
            )
            self.assertTrue(
                harness.get("ok"),
                msg=json.dumps(harness, indent=2, ensure_ascii=False),
            )
            report = run_reasoner_gate(
                tbox_paths=TBOX,
                abox_path=abox,
                report_path=Path(tmp) / "reasoner.json",
            )
            hermit = report.get("hermit") or {}
            if hermit.get("available") and "error" not in hermit:
                self.assertFalse(report.get("ok"))
                failures = " ".join(report.get("failures") or [])
                self.assertIn(SEMANTIC_POISON_PROP, failures)

    def test_prove_repairs_rejects_no_llm(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ontosyn_prove_nollm_") as tmp:
            with self.assertRaises(ValueError) as ctx:
                run_prove_repairs(
                    output_root=Path(tmp),
                    meta_task_config=META,
                    tbox_paths=TBOX,
                    fixture_path=FIXTURE,
                    model="gpt-5.2",
                    max_ruff_repairs=2,
                    allow_llm=False,
                    scripts_source=CANDIDATE_SCRIPTS,
                )
            self.assertIn("LLM", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
