"""Offline tests for the OntoSynthesis main-only semantic MCP repair loop."""

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
from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    autofix_ruff_on_scripts,
    run_ruff_on_scripts,
)
from src.agents.scripts_and_prompts_generation.semantic_mcp_loop_ontosynthesis import (
    SEMANTIC_POISON_PROP,
    _context_from_scripts,
    exercise_level1_fail,
    exercise_semantic_fail,
    package_semantic_feedback,
    _primary_ordering_property,
    _probe_artifacts_in_turtle,
    _react_mcp_config_path,
    _semantic_ontology_contract,
    _select_react_output_ttls,
    _tbox_fixture_inventory,
    _write_ontosynthesis_mcp_launcher,
    _write_ontosynthesis_react_mcp_config,
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
    def test_react_output_prefers_entity_closures_over_bootstrap_top(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            top = output / "top.ttl"
            first = output / "entity-a.ttl"
            second = output / "entity-b.ttl"
            for path in (top, first, second):
                path.write_text("<urn:s> <urn:p> <urn:o> .\n", encoding="utf-8")

            self.assertEqual(
                _select_react_output_ttls(output),
                [first, second],
            )

    def test_react_output_uses_top_only_as_bootstrap_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            top = output / "top.ttl"
            top.write_text("<urn:s> <urn:p> <urn:o> .\n", encoding="utf-8")

            self.assertEqual(_select_react_output_ttls(output), [top])

    def test_oracle_guard_rejects_validator_probe_artifacts(self) -> None:
        ttl = """
@prefix ex: <https://example.test/> .
ex:item ex:label "Validator ChemicalSynthesis" .
"""
        self.assertIn(
            "Validator ChemicalSynthesis",
            _probe_artifacts_in_turtle(ttl),
        )

    def test_stage_behavior_probe_uses_isolated_registry(self) -> None:
        import importlib

        from rdflib import URIRef
        from rdflib.namespace import RDF

        from src.agents.scripts_and_prompts_generation import (
            agentic_generation_validation,
        )
        from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
            generate_deterministic_script_slice,
        )

        with tempfile.TemporaryDirectory(prefix="ontosyn_stage_probe_") as tmp:
            root = Path(tmp)
            context = build_agentic_generation_context(
                ontology_name="ontosynthesis",
                meta_task_config_path=META,
                output_root=root,
                write_files=True,
            )
            generate_deterministic_script_slice(context)
            module = agentic_generation_validation._import_generated_main_module(
                Path(context.scripts_dir), "ontosynthesis"
            )
            runtime = importlib.import_module(
                f"{module.__package__}._fixed_rdf_runtime"
            )
            graph = runtime.retained_graph()
            marker = URIRef("urn:test:canonical-marker")
            graph.add((marker, RDF.type, URIRef("urn:test:Type")))
            before = agentic_generation_validation._graph_fingerprint(graph)
            entity_path = next(
                Path(context.scripts_dir).glob("*_creation_entities.py")
            )
            relative = entity_path.relative_to(root).as_posix()

            agentic_generation_validation._stage_artifact_contract_report(
                context, [relative]
            )

            self.assertEqual(
                before,
                agentic_generation_validation._graph_fingerprint(graph),
            )
            self.assertFalse(
                any(
                    "Validator" in str(value)
                    for triple in graph
                    for value in triple
                )
            )

    def test_same_runtime_file_isolated_between_import_packages(self) -> None:
        import importlib.util
        import sys
        import types
        from uuid import uuid4

        from rdflib import URIRef
        from rdflib.namespace import RDF

        runtime_path = (
            ROOT
            / "src"
            / "agents"
            / "scripts_and_prompts_generation"
            / "fixed_rdf_runtime.py"
        )
        runtimes = []
        for _ in range(2):
            package_name = f"_runtime_isolation_{uuid4().hex}"
            package = types.ModuleType(package_name)
            package.__path__ = [str(runtime_path.parent)]  # type: ignore[attr-defined]
            sys.modules[package_name] = package
            module_name = f"{package_name}.fixed_rdf_runtime"
            spec = importlib.util.spec_from_file_location(module_name, runtime_path)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader if spec else None)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            runtimes.append(module)

        first_graph = runtimes[0].retained_graph()
        second_graph = runtimes[1].retained_graph()
        first_graph.add(
            (URIRef("urn:test:first"), RDF.type, URIRef("urn:test:Type"))
        )

        self.assertIsNot(first_graph, second_graph)
        self.assertEqual(0, len(second_graph))
        self.assertNotEqual(runtimes[0]._REGISTRY_KEY, runtimes[1]._REGISTRY_KEY)

    def test_runtime_hygiene_probe_restores_shared_graph(self) -> None:
        from rdflib import Literal, URIRef
        from rdflib.namespace import RDF
        import importlib

        from src.agents.scripts_and_prompts_generation import (
            agentic_generation_validation,
        )

        with tempfile.TemporaryDirectory(prefix="ontosyn_probe_isolation_") as tmp:
            root = Path(tmp)
            context = build_agentic_generation_context(
                ontology_name="ontosynthesis",
                meta_task_config_path=META,
                output_root=root,
                write_files=True,
            )
            from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
                generate_deterministic_script_slice,
            )

            generate_deterministic_script_slice(context)
            module = agentic_generation_validation._import_generated_main_module(
                Path(context.scripts_dir), "ontosynthesis"
            )
            runtime = importlib.import_module(
                f"{module.__package__}._fixed_rdf_runtime"
            )
            graph = runtime.retained_graph()
            marker = URIRef("urn:test:preexisting")
            graph.add((marker, RDF.type, URIRef("urn:test:Type")))
            graph.add((marker, URIRef("urn:test:value"), Literal("preserve")))
            before = agentic_generation_validation._graph_fingerprint(graph)

            agentic_generation_validation._runtime_graph_hygiene_report(context)

            self.assertEqual(
                before,
                agentic_generation_validation._graph_fingerprint(graph),
            )
            self.assertFalse(
                any(
                    "Validator" in str(value)
                    for triple in graph
                    for value in triple
                )
            )

    def test_reasoner_ignores_tbox_schema_copied_into_abox_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tbox = root / "tbox.ttl"
            abox = root / "abox.ttl"
            tbox.write_text(
                """
@prefix ex: <https://example.org/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
ex:Entity a owl:Class ;
    ex:instanceIntegrityRule "one_individual_per_entity" .
ex:relatesTo a owl:ObjectProperty .
""",
                encoding="utf-8",
            )
            abox.write_text(
                tbox.read_text(encoding="utf-8") + "\nex:item a ex:Entity .\n",
                encoding="utf-8",
            )

            report = run_reasoner_gate(
                tbox_paths=[tbox],
                abox_path=abox,
                report_path=root / "reasoner.json",
            )

            self.assertEqual(
                [],
                (report.get("details") or {}).get("unknown_properties"),
                msg=json.dumps(report, indent=2),
            )

    def test_copied_checkpoint_receives_current_enforced_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ontosyn_checkpoint_") as tmp:
            root = Path(tmp)
            source_root = root / "source"
            source_context = build_agentic_generation_context(
                ontology_name="ontosynthesis",
                meta_task_config_path=META,
                output_root=source_root,
                write_files=True,
            )
            source = Path(source_context.scripts_dir)
            (source / "_fixed_rdf_runtime.py").write_text(
                "def add_object_property(subject_iri, predicate_iri, object_iri):\n"
                "    return None\n",
                encoding="utf-8",
            )
            destination = root / "destination"

            context = _context_from_scripts(
                scripts_dir=source,
                meta_task_config=META,
                output_root=destination,
            )

            runtime = (
                Path(context.scripts_dir) / "_fixed_rdf_runtime.py"
            ).read_text(encoding="utf-8")
            contract = Path(
                context.scripts_dir, "_relationship_contract.json"
            )
            self.assertNotIn("def add_object_property", runtime)
            self.assertIn("package_relationship_capabilities", runtime)
            self.assertTrue(contract.is_file())
            self.assertTrue(
                json.loads(contract.read_text(encoding="utf-8")).get(
                    "object_properties"
                )
            )

    def test_launcher_adapts_object_tool_registry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ontosyn_launcher_") as tmp:
            launcher = _write_ontosynthesis_mcp_launcher(Path(tmp))
            text = launcher.read_text(encoding="utf-8")

        self.assertIn('getattr(exported, "tools", None)', text)
        self.assertIn("for tool_name, tool_fn in registry.items()", text)
        self.assertNotIn("load_from_turtle_file", text)
        self.assertIn('@server.prompt(name="instruction")', text)

    def test_react_mcp_config_does_not_serialize_parent_secrets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ontosyn_config_") as tmp:
            root = Path(tmp)
            config_path = root / "mcp.json"
            previous = os.environ.get("OPENAI_API_KEY")
            os.environ["OPENAI_API_KEY"] = "must-not-be-serialized"
            try:
                _write_ontosynthesis_react_mcp_config(
                    artifact_root=root,
                    config_path=config_path,
                    data_dir=root / "data",
                )
            finally:
                if previous is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = previous
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            env = payload["llm_created_mcp"]["env"]

        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertTrue(all(key.startswith("TWA_") for key in env))

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

    def test_semantic_contract_projects_tbox_comments_without_domain_rules(self) -> None:
        projected = _semantic_ontology_contract(self.context)
        source_properties = self.context.parsed.get("properties") or {}
        expected_comments = {
            str(meta.get("iri") or ""): str(meta.get("comment") or "")
            for meta in source_properties.values()
            if isinstance(meta, dict) and str(meta.get("comment") or "")
        }
        projected_comments = {
            str(item.get("iri") or ""): str(item.get("comment") or "")
            for item in projected["tbox_property_rules"]
            if str(item.get("comment") or "")
        }

        self.assertTrue(expected_comments)
        self.assertEqual(projected_comments, expected_comments)

    def test_react_mcp_configs_are_isolated_by_run(self) -> None:
        first = _react_mcp_config_path(
            artifact_root=Path("candidate-a"),
            data_dir=Path("runtime-a"),
        )
        second = _react_mcp_config_path(
            artifact_root=Path("candidate-b"),
            data_dir=Path("runtime-b"),
        )

        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, ROOT / "configs")
        self.assertTrue(first.name.startswith("test_mcp_ontosynthesis_semantic_"))

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
