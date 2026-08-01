"""Focused tests for OM-2 validation and mock content scoring."""

from __future__ import annotations

import json
import os
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
    score_graph_content,
    score_hint_content,
)
from src.agents.scripts_and_prompts_generation.fixed_rdf_runtime import (
    export_graph_result,
    initialize_retained_graph,
    retained_graph,
    scoped_memory_paths,
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
    def test_scripts_source_does_not_trigger_unrelated_binding_repairs(self) -> None:
        import inspect

        source = inspect.getsource(
            __import__(
                "src.agents.scripts_and_prompts_generation.semantic_mcp_loop_ontosynthesis",
                fromlist=["run_outer_loop"],
            ).run_outer_loop
        )

        self.assertIn("if scripts_source is not None", source)
        self.assertIn("else _repair_prompt_runtime_bindings", source)
        self.assertIn("external_source_package_deferred", source)

    def test_fixed_runtime_resume_is_idempotent_and_export_is_abox_only(self) -> None:
        graph = retained_graph()
        graph.remove((None, None, None))
        initialize_retained_graph()
        graph.parse(
            data="""
@prefix ex: <https://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
ex:Entity a owl:Class .
ex:item a ex:Entity .
""",
            format="turtle",
        )
        before = len(graph)

        resumed = initialize_retained_graph()
        exported = export_graph_result(graph)

        self.assertEqual(before, resumed["total_triples"])
        self.assertIn("ex:item", exported["ttl"])
        self.assertNotIn("owl:Class", exported["ttl"])
        self.assertFalse(exported["includes_schema"])

    def test_fixed_runtime_public_initializer_has_no_reset_mode(self) -> None:
        import inspect

        self.assertEqual(
            ["source_path"],
            list(inspect.signature(initialize_retained_graph).parameters),
        )

    def test_fixed_runtime_scoped_memory_path_matches_pipeline_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("TWA_AGENTIC_DATA_DIR")
            os.environ["TWA_AGENTIC_DATA_DIR"] = tmp
            try:
                memory_path, _ = scoped_memory_paths(
                    "case-hash",
                    "Primary synthesis α",
                )
            finally:
                if previous is None:
                    os.environ.pop("TWA_AGENTIC_DATA_DIR", None)
                else:
                    os.environ["TWA_AGENTIC_DATA_DIR"] = previous

        self.assertEqual(
            Path(tmp) / "case-hash" / "memory" / "Primary_synthesis_alpha.ttl",
            memory_path,
        )

    def test_graph_score_ignores_shared_tbox_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold = root / "gold.ttl"
            predicted = root / "predicted.ttl"
            shared_schema = """
@prefix ex: <https://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
ex:Entity a owl:Class .
"""
            gold.write_text(
                shared_schema + 'ex:gold a ex:Entity ; ex:value "expected" .\n',
                encoding="utf-8",
            )
            predicted.write_text(
                shared_schema + 'ex:predicted a ex:Entity ; ex:value "wrong" .\n',
                encoding="utf-8",
            )

            report = score_graph_content(gold, predicted)

            self.assertGreater(report["overall"]["tp"], 0)
            self.assertEqual(1, report["overall"]["fp"])
            self.assertEqual(1, report["overall"]["fn"])

    def test_export_metadata_cannot_override_abox_projection(self) -> None:
        graph = retained_graph()
        graph.remove((None, None, None))
        initialize_retained_graph()
        graph.parse(
            data="""
@prefix ex: <https://example.test/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
ex:Entity a owl:Class .
ex:item a ex:Entity .
""",
            format="turtle",
        )

        exported = export_graph_result(graph, ttl=graph.serialize(format="turtle"))

        self.assertNotIn("owl:Class", exported["ttl"])

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

    def test_content_gate_keeps_structural_scores_diagnostic(self) -> None:
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
        self.assertTrue(decision["accepted"])
        self.assertEqual([], decision["failures"])
        self.assertEqual(
            "semantic_soft_gate_with_deterministic_diagnostics",
            decision["policy"],
        )
        feedback = package_content_feedback(report, decision, {
            "hints": champion,
            "graph": {"overall": {"f1": 1.0}},
        })
        self.assertIn("ACCEPTED", feedback)
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

    def test_generated_om2_contract_uses_fixed_runtime(self) -> None:
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
            (broken_dir / "_fixed_om2_runtime.py").unlink()
            broken = validate_generated_artifacts(
                contract_bundle=context.contract,
                scripts_dir=broken_dir,
                prompts_dir=None,
            )
            self.assertFalse(broken["ok"])
            self.assertTrue(
                any("fixed OM-2 runtime" in item for item in broken["failures"])
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
