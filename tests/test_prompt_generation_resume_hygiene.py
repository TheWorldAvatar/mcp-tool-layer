"""Resume/validation hygiene for partial extraction-prompt packages.

A leftover markdown file (old KG-building prompts, retired iterations, or a
hollow iterations.json) must not crash prompt generation with
`iteration None`. Empty T-Box scope on a real planned iteration is still
refused.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.extraction_prompt_generation.compile.artifact_compiler import (
    _legacy_adapter,
    _runtime_blueprint,
)
from src.extraction_prompt_generation.compile.context import (
    build_agentic_generation_context,
)
from src.extraction_prompt_generation.compile.iteration_plan import (
    compile_iteration_plan,
)
from src.extraction_prompt_generation.config.domain_config import (
    load_domain_generation_config,
)
from src.extraction_prompt_generation.generate.artifact_state import (
    ArtifactStateStore,
)
from src.extraction_prompt_generation.generate.authoring import (
    _editable_artifacts,
    run_pure_llm_generation_rounds,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.scope import (
    _canonical_iteration_filename_token,
    _planned_extraction_prompt_paths,
    _prompt_can_build_generation_contract,
    _prompt_iteration_spec,
    _unplanned_prompt_artifact_paths,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.tbox_slice import (
    _prompt_tbox_slice,
)
from src.extraction_prompt_generation.paths import (
    repository_root,
    resolve_domain_config_path,
)
from src.extraction_prompt_generation.planning.semantic_planner import (
    plan_domain_semantics,
)
from src.extraction_prompt_generation.validate.report import (
    build_validation_report,
)


def _ontology(role: str = "main"):
    return SimpleNamespace(name="ontosynthesis", role=role)


def _context(root: Path, *, role: str = "main", blueprint=None):
    prompts = root / "prompts" / "ontosynthesis"
    prompts.mkdir(parents=True, exist_ok=True)
    return (
        SimpleNamespace(
            output_root=str(root),
            prompts_dir=str(prompts),
            ontology=_ontology(role),
            iteration_blueprint=blueprint if blueprint is not None else {},
            parsed={"classes": {}, "properties": {}},
            contract={},
        ),
        prompts,
    )


def _scoped_iteration(number, local: str = "ReactionStep"):
    return {
        "iteration_number": number,
        "semantic_scope": {"classes": [{"local": local}]},
        "responsibilities": {"classes": [local], "object_properties": []},
    }


class PromptGenerationResumeHygiene(unittest.TestCase):
    def test_canonical_token_normalizes_integral_floats(self) -> None:
        self.assertEqual(_canonical_iteration_filename_token(2), "2")
        self.assertEqual(_canonical_iteration_filename_token(2.0), "2")
        self.assertEqual(_canonical_iteration_filename_token("3.1"), "3_1")

    def test_lookup_matches_integer_filename_when_plan_has_float(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context, _prompts = _context(
                root,
                blueprint={"iterations": [_scoped_iteration(2.0)]},
            )
            spec = _prompt_iteration_spec(context, Path("EXTRACTION_ITER_2.md"))
            self.assertEqual(spec.get("iteration_number"), 2.0)
            self.assertTrue(
                _prompt_can_build_generation_contract(
                    context, Path("EXTRACTION_ITER_2.md")
                )
            )

    def test_lookup_falls_back_to_blueprint_when_disk_plan_is_hollow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_dir = root / "iterations" / "ontosynthesis"
            plan_dir.mkdir(parents=True)
            (plan_dir / "iterations.json").write_text("{}", encoding="utf-8")
            context, _prompts = _context(
                root,
                blueprint={"iterations": [_scoped_iteration(3)]},
            )
            spec = _prompt_iteration_spec(context, Path("EXTRACTION_ITER_3.md"))
            self.assertEqual(spec.get("iteration_number"), 3)

    def test_lookup_falls_back_when_disk_plan_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_dir = root / "iterations" / "ontosynthesis"
            plan_dir.mkdir(parents=True)
            (plan_dir / "iterations.json").write_text("{not-json", encoding="utf-8")
            context, _prompts = _context(
                root,
                blueprint={"iterations": [_scoped_iteration(2)]},
            )
            spec = _prompt_iteration_spec(context, Path("EXTRACTION_ITER_2.md"))
            self.assertEqual(spec.get("iteration_number"), 2)

    def test_planned_paths_ignore_kg_building_and_retired_iterations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context, prompts = _context(
                root,
                blueprint={"iterations": [_scoped_iteration(2)]},
            )
            (prompts / "EXTRACTION_ITER_1.md").write_text("iter1", encoding="utf-8")
            (prompts / "EXTRACTION_ITER_2.md").write_text("iter2", encoding="utf-8")
            (prompts / "EXTRACTION_ITER_9.md").write_text("retired", encoding="utf-8")
            (prompts / "KG_BUILDING_ITER_1.md").write_text("kg", encoding="utf-8")
            (prompts / "README.md").write_text("notes", encoding="utf-8")

            planned = [path.name for path in _planned_extraction_prompt_paths(context)]
            leftover = [
                path.name for path in _unplanned_prompt_artifact_paths(context)
            ]
            editable = [path.name for path in _editable_artifacts(context)]

            self.assertEqual(planned, ["EXTRACTION_ITER_1.md", "EXTRACTION_ITER_2.md"])
            self.assertEqual(editable, planned)
            self.assertEqual(
                leftover,
                ["EXTRACTION_ITER_9.md", "KG_BUILDING_ITER_1.md", "README.md"],
            )

    def test_iter1_is_planned_for_main_even_without_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context, _prompts = _context(Path(tmp), blueprint={})
            self.assertTrue(
                _prompt_can_build_generation_contract(
                    context, Path("EXTRACTION_ITER_1.md")
                )
            )
            self.assertFalse(
                _prompt_can_build_generation_contract(
                    context, Path("EXTRACTION_ITER_2.md")
                )
            )

    def test_iter1_is_not_special_cased_for_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context, _prompts = _context(Path(tmp), role="extension", blueprint={})
            self.assertFalse(
                _prompt_can_build_generation_contract(
                    context, Path("EXTRACTION_ITER_1.md")
                )
            )

    def test_empty_iteration_spec_still_refuses_invented_tbox_scope(self) -> None:
        context = SimpleNamespace(parsed={"classes": {}, "properties": {}}, contract={})
        with self.assertRaises(ValueError) as raised:
            _prompt_tbox_slice(context, {})
        message = str(raised.exception)
        self.assertIn("matching iteration spec", message)
        self.assertIn("refusing to invent scope", message)

    def test_planned_iteration_without_scope_still_refuses_fallback(self) -> None:
        context = SimpleNamespace(parsed={"classes": {}, "properties": {}}, contract={})
        with self.assertRaises(ValueError) as raised:
            _prompt_tbox_slice(
                context,
                {"iteration_number": 2, "semantic_scope": {}, "responsibilities": {}},
            )
        self.assertIn("for iteration 2", str(raised.exception))

    def test_disk_plan_is_used_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan_dir = root / "iterations" / "ontosynthesis"
            plan_dir.mkdir(parents=True)
            (plan_dir / "iterations.json").write_text(
                json.dumps({"iterations": [_scoped_iteration(4, "ChemicalInput")]}),
                encoding="utf-8",
            )
            context, _prompts = _context(root, blueprint={"iterations": []})
            spec = _prompt_iteration_spec(context, Path("EXTRACTION_ITER_4.md"))
            self.assertEqual(spec.get("iteration_number"), 4)
            classes = (spec.get("semantic_scope") or {}).get("classes") or []
            self.assertEqual(classes[0]["local"], "ChemicalInput")


def _block_llm(*args, **kwargs):
    raise AssertionError("LLM invoked during no-LLM resume smoke")


def _write_partial_prompt_package(context, plan: dict) -> None:
    prompts = Path(context.prompts_dir)
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "EXTRACTION_ITER_1.md").write_text(
        "Extract top entities.\n{paper_content}\n",
        encoding="utf-8",
    )
    for iteration in plan.get("iterations") or []:
        token = _canonical_iteration_filename_token(
            iteration.get("iteration_number")
        )
        if token:
            (prompts / f"EXTRACTION_ITER_{token}.md").write_text(
                "Extract iteration hints.\n"
                "{paper_content}\n{entity_label}\n{entity_uri}\n"
                "{accumulated_hints}\n",
                encoding="utf-8",
            )
        if token and iteration.get("has_pre_extraction"):
            (prompts / f"PRE_EXTRACTION_ITER_{token}.md").write_text(
                "Pre-extract candidates.\n{paper_content}\n",
                encoding="utf-8",
            )
        for sub_iteration in iteration.get("sub_iterations") or []:
            sub_token = _canonical_iteration_filename_token(
                sub_iteration.get("iteration_number")
            )
            if sub_token:
                (prompts / f"EXTRACTION_ITER_{sub_token}.md").write_text(
                    "Enrichment hints.\n"
                    "{paper_content}\n{entity_label}\n{entity_uri}\n",
                    encoding="utf-8",
                )
    (prompts / "KG_BUILDING_ITER_1.md").write_text(
        "legacy kg-building prompt\n",
        encoding="utf-8",
    )
    (prompts / "EXTRACTION_ITER_99.md").write_text(
        "retired iteration leftover\n",
        encoding="utf-8",
    )
    plan_dir = Path(context.output_root) / "iterations" / context.ontology.name
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "iterations.json").write_text("{}", encoding="utf-8")


class PartialPackageResumeSmoke(unittest.TestCase):
    """Replay the user crash: resume a half-finished ontosynthesis prompt dir."""

    def setUp(self) -> None:
        patches = [
            patch(
                "src.extraction_prompt_generation.llm.invoke.invoke_json",
                side_effect=_block_llm,
            ),
            patch(
                "src.extraction_prompt_generation.planning.semantic_planner.invoke_json",
                side_effect=_block_llm,
            ),
            patch(
                "src.extraction_prompt_generation.planning.semantic_planner._invoke",
                side_effect=_block_llm,
            ),
        ]
        for item in patches:
            self.addCleanup(item.stop)
            item.start()

    def _compile_ontosynthesis(self, tmp: Path):
        config = load_domain_generation_config(
            resolve_domain_config_path(ontology_name="ontosynthesis"),
            repository_root=repository_root(),
        )
        adapter = _legacy_adapter(config, blueprint_path=None)
        adapter_path = tmp / "meta_task_adapter.json"
        adapter_path.write_text(
            json.dumps(adapter, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        context = build_agentic_generation_context(
            ontology_name="ontosynthesis",
            meta_task_config_path=adapter_path,
            output_root=tmp / "generated",
            write_files=False,
        )
        contract = dict(context.contract)
        class_local = "ChemicalSynthesis"
        class_spec = (context.parsed.get("classes") or {}).get(class_local) or {}
        contract["top_entity"] = {
            "class_local": class_local,
            "class_iri": str(class_spec.get("iri") or ""),
            "status": "known",
            "source": "resume_smoke",
        }
        context = replace(context, contract=contract)
        decisions = plan_domain_semantics(
            config=config,
            parsed=context.parsed,
            contract=context.contract,
            planning_dir=None,
            top_entity_owner="iteration1",
            selected_root={
                "class_local": class_local,
                "rationale": "Smoke-selected primary synthesis root.",
                "evidence": ["ChemicalSynthesis", "hasChemicalOutput"],
            },
        )
        plan = compile_iteration_plan(
            blueprint=_runtime_blueprint(config, decisions),
            parsed=context.parsed,
            contract=context.contract,
            ontology_name="ontosynthesis",
            blueprint_provenance={"source": "partial_package_resume_smoke"},
        )
        self.assertTrue(plan.get("iterations"))
        return replace(context, iteration_blueprint=plan), plan

    def test_checkpoint_resume_ignores_leftover_prompt_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="epg-resume-smoke-") as raw_tmp:
            context, plan = self._compile_ontosynthesis(Path(raw_tmp))
            _write_partial_prompt_package(context, plan)
            leftovers = {
                path.name for path in _unplanned_prompt_artifact_paths(context)
            }
            self.assertIn("KG_BUILDING_ITER_1.md", leftovers)
            self.assertIn("EXTRACTION_ITER_99.md", leftovers)

            planned = _editable_artifacts(context)
            self.assertTrue(planned)
            self.assertNotIn("KG_BUILDING_ITER_1.md", [path.name for path in planned])
            store = ArtifactStateStore(context.output_root, context.ontology.name)
            store.initialize(planned)
            for path in planned:
                store.transition(path, "passed")

            report = build_validation_report(
                context,
                write_report=False,
                prompts_required=True,
                include_prompt_checks=True,
            )
            warning_text = "\n".join(str(item) for item in (report.get("warnings") or []))
            self.assertIn("KG_BUILDING_ITER_1.md", warning_text)
            self.assertIn("EXTRACTION_ITER_99.md", warning_text)
            self.assertNotIn("iteration None", json.dumps(report))

            result = run_pure_llm_generation_rounds(context)
            self.assertEqual(result.get("mode"), "pure_llm_checkpoint_resume")
            self.assertTrue(result.get("checkpoint_preserved"))
            self.assertTrue(result.get("resumed_passed"))


if __name__ == "__main__":
    unittest.main()
