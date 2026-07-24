"""Focused tests for OM-2 validation and mock content scoring."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_script_slice,
)
from src.agents.scripts_and_prompts_generation.content_fixture_score import (
    load_predicted_hints,
    score_hint_content,
)
from src.agents.scripts_and_prompts_generation.generation_contracts import (
    validate_generated_artifacts,
)
from src.agents.scripts_and_prompts_generation.semantic_mcp_loop_ontosynthesis import (
    _content_gate_decision,
    package_content_feedback,
)
from scripts.validate_abox_with_reasoner import validate


ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "configs/meta_task/meta_task_config.json"


class TestContentPromptEnhancement(unittest.TestCase):
    def test_hint_content_score_reports_missing_and_unexpected(self) -> None:
        gold = {
            "Add": [
                {
                    "label": "Add solvent",
                    "hasOrder": 1,
                    "hasAddedChemicalInput_label": "DMF",
                }
            ]
        }
        predicted = {
            "Add": [
                {
                    "label": "Add solvent",
                    "hasOrder": 1,
                    "hasAddedChemicalInput_label": "MeOH",
                }
            ]
        }
        report = score_hint_content(gold, predicted)
        self.assertFalse(report["ok"])
        self.assertEqual(report["overall"]["tp"], 2)
        self.assertEqual(report["overall"]["fn"], 1)
        self.assertEqual(report["overall"]["fp"], 1)
        feedback = package_content_feedback({"hints": report, "graph": {}})
        self.assertIn("dmf", feedback)
        self.assertIn("meoh", feedback)
        self.assertIn("not Python script repair", feedback)

    def test_unscored_enrichment_is_not_a_false_positive(self) -> None:
        report = score_hint_content(
            {"ChemicalInput": {"label": "DMF"}},
            {
                "ChemicalInput": {
                    "label": "DMF",
                    "hasAlternativeNames": "N,N-dimethylformamide",
                },
                "MetalOrganicPolyhedron": {"label": "UMC-1"},
            },
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["overall"]["fp"], 0)

    def test_alias_score_is_delimiter_aware_and_entity_scoped(self) -> None:
        report = score_hint_content(
            {
                "ChemicalInput": [
                    {
                        "label": "DMF",
                        "hasAlternativeNames": "N,N-dimethylformamide;Dimethylformamide",
                    },
                    {"label": "water"},
                ]
            },
            {
                "ChemicalInput": [
                    {
                        "label": "DMF",
                        "hasAlternativeNames": [
                            "Dimethylformamide",
                            "N,N-dimethylformamide",
                            "DMFA",
                        ],
                    },
                    {
                        "label": "water",
                        "hasAlternativeNames": "oxidane",
                    },
                ]
            },
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["overall"]["fp"], 0)

    def test_content_gate_rejects_critical_and_champion_regressions(self) -> None:
        champion = score_hint_content(
            {"Add": {"label": "Add DMF", "hasOrder": 1}},
            {"Add": {"label": "Add DMF", "hasOrder": 1}},
        )
        candidate = score_hint_content(
            {"Add": {"label": "Add DMF", "hasOrder": 1}},
            {"Add": {"label": "Add DMF"}},
        )
        report = {
            "hints": candidate,
            "graph": {"overall": {"f1": 1.0, "recall": 1.0}},
        }
        decision = _content_gate_decision(
            content_report=report,
            fixture={
                "critical_slots": [
                    {"class": "Add", "property": "hasOrder", "policy": "zero_fn"}
                ]
            },
            champion_report={
                "hints": champion,
                "graph": {"overall": {"f1": 1.0, "recall": 1.0}},
            },
            semantic_ok=True,
            hint_threshold=0.0,
            graph_threshold=0.0,
        )
        self.assertFalse(decision["accepted"])
        self.assertIn("critical_slots", decision["failures"])
        self.assertIn("champion_preserve_set", decision["failures"])
        feedback = package_content_feedback(report, decision, {
            "hints": champion,
            "graph": {"overall": {"f1": 1.0}},
        })
        self.assertIn("REJECTED", feedback)
        self.assertIn("candidate delta", feedback)

    def test_explicit_absent_class_is_scored_as_false_positive(self) -> None:
        report = score_hint_content(
            {
                "__absent_classes__": ["Yield"],
                "ChemicalOutput": {"label": "UMC-1"},
            },
            {
                "ChemicalOutput": {"label": "UMC-1"},
                "Yield": {"label": "UMC-1"},
            },
        )
        self.assertFalse(report["ok"])
        self.assertEqual(report["overall"]["fp"], 1)
        self.assertEqual(report["unexpected"][0]["class"], "Yield")

    def test_predicted_hints_merge_fields_across_iterations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ontosyn_hint_merge_") as tmp:
            case_dir = Path(tmp)
            run_dir = case_dir / "mcp_run"
            run_dir.mkdir()
            (run_dir / "iter2_hints_UMC-1.txt").write_text(
                json.dumps(
                    {
                        "Add": {
                            "label": "Add DMF",
                            "hasAddedChemicalInput_label": "DMF",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "iter3_hints_UMC-1.txt").write_text(
                json.dumps({"Add": {"label": "Add DMF", "hasOrder": 1}}),
                encoding="utf-8",
            )
            merged = load_predicted_hints(case_dir)
            self.assertEqual(
                merged["Add"][0]["hasAddedChemicalInput_label"],
                "DMF",
            )
            self.assertEqual(merged["Add"][0]["hasOrder"], 1)

    def test_generated_om2_contract_passes_and_missing_helper_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ontosyn_om2_contract_") as tmp:
            root = Path(tmp)
            context = build_agentic_generation_context(
                ontology_name="ontosynthesis",
                meta_task_config_path=META,
                output_root=root,
                write_files=True,
            )
            generate_deterministic_script_slice(context)
            valid = validate_generated_artifacts(
                contract_bundle=context.contract,
                scripts_dir=context.scripts_dir,
                prompts_dir=None,
            )
            self.assertTrue(valid["ok"], msg="\n".join(valid["failures"]))

            broken_dir = root / "broken"
            shutil.copytree(context.scripts_dir, broken_dir)
            base = next(broken_dir.glob("*_creation_base.py"))
            text = base.read_text(encoding="utf-8").replace(
                "def _find_or_create_om2_quantity(",
                "def _broken_quantity_builder(",
                1,
            )
            base.write_text(text, encoding="utf-8")
            broken = validate_generated_artifacts(
                contract_bundle=context.contract,
                scripts_dir=broken_dir,
                prompts_dir=None,
            )
            self.assertFalse(broken["ok"])
            self.assertTrue(
                any("_find_or_create_om2_quantity" in item for item in broken["failures"])
            )

    def test_abox_gate_rejects_incomplete_om2_quantity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ontosyn_om2_abox_") as tmp:
            root = Path(tmp)
            tbox = root / "tbox.ttl"
            abox = root / "abox.ttl"
            tbox.write_text(
                """
@prefix ex: <https://example.com/> .
@prefix om: <http://www.ontology-of-units-of-measure.org/resource/om-2/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

ex:Step a owl:Class .
om:Duration a owl:Class .
ex:hasDuration a owl:ObjectProperty ;
  rdfs:domain ex:Step ;
  rdfs:range om:Duration .
om:hasNumericalValue a owl:DatatypeProperty .
om:hasUnit a owl:ObjectProperty .
""".strip(),
                encoding="utf-8",
            )
            abox.write_text(
                """
@prefix ex: <https://example.com/> .
@prefix om: <http://www.ontology-of-units-of-measure.org/resource/om-2/> .

ex:step1 a ex:Step ; ex:hasDuration ex:duration1 .
ex:duration1 a om:Duration .
""".strip(),
                encoding="utf-8",
            )
            report = validate([tbox], [abox], run_hermit=False)
            violations = (report.get("details") or {}).get("om2_quantity_violations") or []
            self.assertFalse(report["ok"])
            self.assertEqual(len(violations), 2)


if __name__ == "__main__":
    unittest.main()
