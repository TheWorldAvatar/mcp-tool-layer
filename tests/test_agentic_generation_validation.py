from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
import tempfile
from unittest.mock import patch

from rdflib import URIRef
from rdflib.namespace import RDF

from src.agents.scripts_and_prompts_generation import pure_llm_generation
from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_prompt_slice,
    generate_deterministic_script_slice,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    _init_memory_ast_evidence,
    _foreign_symbol_report,
    _iter1_kg_prompt_execution_contract_report,
    _kg_iteration_owned_scope_report,
    _iteration_prompt_schema_contract_report,
    _ordered_violation_code_report,
    _prompt_tbox_fidelity_report,
    _prompt_tool_signature_alignment_report,
    _required_operation_fixture_triples,
    _seed_existing_operation_targets,
    _relationship_binding_evidence,
    _runtime_graph_hygiene_report,
    _stage_artifact_contract_report,
    build_validation_report,
    empty_existing_check_probe_failures,
)
from src.agents.scripts_and_prompts_generation.reuse_policy import (
    EXISTING_CHECK_EVIDENCE_REQUIRED_SCOPES,
)
from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_validation_observation,
)


class TestAgenticGenerationValidation(unittest.TestCase):
    def test_atomic_creator_probe_seeds_typed_existing_target(self) -> None:
        from rdflib import Graph

        graph = Graph()
        _seed_existing_operation_targets(
            graph,
            {
                "required_edges": [
                    {
                        "target_resolution": "existing_iri_parameter",
                        "parameter_name": "container_iri",
                        "container_class_iris": ["urn:test:Container"],
                    }
                ]
            },
            {"container_iri": "urn:test:existing-container"},
        )

        self.assertIn(
            (
                URIRef("urn:test:existing-container"),
                RDF.type,
                URIRef("urn:test:Container"),
            ),
            graph,
        )

    def test_generated_runtime_signature_resolves_creator_from_imported_module(
        self,
    ) -> None:
        entities = ModuleType("generated_entities")

        def create_Member(label: str, order: int) -> str:
            return f"{label}:{order}"

        entities.create_Member = create_Member
        main = ModuleType("generated_main")
        main.entities = entities

        signature = pure_llm_generation._generated_runtime_signature(
            main, "create_Member"
        )

        self.assertEqual(list(signature.parameters), ["label", "order"])

    def test_prompt_signature_alignment_rejects_semantic_property_as_keyword(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prompts = Path(tmp) / "prompts"
            scripts = Path(tmp) / "scripts"
            prompts.mkdir()
            scripts.mkdir()
            (scripts / "main.py").write_text(
                "def create_Member(label: str, order: int) -> str:\n"
                "    return f'{label}:{order}'\n",
                encoding="utf-8",
            )
            path = prompts / "KG_BUILDING_ITER_2.md"
            path.write_text(
                "  - create_Member(label: str, hasOrder: int) -> str",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                prompts_dir=str(prompts),
                scripts_dir=str(scripts),
                ontology=SimpleNamespace(name="synthetic"),
            )
            contract = {
                "agent_tool_contract": {
                    "creator_tools": [
                        {
                            "name": "create_Member",
                            "exact_call_signature": (
                                "create_Member(label: str, order: int) -> str"
                            ),
                            "semantic_parameter_bindings": {
                                "hasOrder": "order"
                            },
                        }
                    ]
                }
            }
            with patch.object(
                pure_llm_generation,
                "_prompt_artifact_generation_contract",
                return_value=contract,
            ):
                failures, _, _ = _prompt_tool_signature_alignment_report(context)
            self.assertTrue(any("keyword drift" in item for item in failures))

            path.write_text(
                "  - create_Member(label: str, order: int) -> str",
                encoding="utf-8",
            )
            with patch.object(
                pure_llm_generation,
                "_prompt_artifact_generation_contract",
                return_value=contract,
            ):
                failures, _, _ = _prompt_tool_signature_alignment_report(context)
            self.assertEqual(failures, [])

    def test_required_operation_fixture_is_compiled_from_generic_contract(self) -> None:
        owner = URIRef("urn:test:owner")
        parent = URIRef("urn:test:parent")
        membership = URIRef("urn:test:membership")
        dependent_predicate = URIRef("urn:test:dependent-predicate")
        dependent_class = URIRef("urn:test:dependent-class")
        second_predicate = URIRef("urn:test:second-dependent-predicate")
        second_class = URIRef("urn:test:second-dependent-class")
        triples = _required_operation_fixture_triples(
            operation_units={
                "units": [
                    {
                        "creator_contract": {
                            "class_iri": "urn:test:owner-class",
                            "required_edges": [
                                {
                                    "predicate_iri": str(membership),
                                    "direction": (
                                        "container_as_subject_owner_as_object"
                                    ),
                                    "target_resolution": (
                                        "existing_iri_parameter"
                                    ),
                                },
                                {
                                    "predicate_iri": str(dependent_predicate),
                                    "direction": (
                                        "owner_as_subject_dependent_as_object"
                                    ),
                                    "target_resolution": "same_operation_create",
                                    "dependent_class_iri": str(dependent_class),
                                },
                            ],
                        }
                    },
                    {
                        "creator_contract": {
                            "class_iri": "urn:test:owner-class",
                            "required_edges": [
                                {
                                    "predicate_iri": str(second_predicate),
                                    "direction": (
                                        "owner_as_subject_dependent_as_object"
                                    ),
                                    "target_resolution": "same_operation_create",
                                    "dependent_class_iri": str(second_class),
                                }
                            ],
                        }
                    }
                ]
            },
            owner_class_iri="urn:test:owner-class",
            owner=owner,
            parent=parent,
            token="owner-1",
        )

        self.assertIn((parent, membership, owner), triples)
        dependent = next(
            object_iri
            for subject, predicate, object_iri in triples
            if subject == owner and predicate == dependent_predicate
        )
        self.assertIn((dependent, RDF.type, dependent_class), triples)
        second = next(
            object_iri
            for subject, predicate, object_iri in triples
            if subject == owner and predicate == second_predicate
        )
        self.assertIn((second, RDF.type, second_class), triples)

    def test_empty_existing_check_probe_is_scope_generic(self) -> None:
        self.assertEqual(
            EXISTING_CHECK_EVIDENCE_REQUIRED_SCOPES, frozenset({"central", "document"})
        )
        reject = {
            "status": "rejected",
            "code": "PROPOSED_ENTITY_EVIDENCE_REQUIRED",
        }
        success = {
            "status": "ok",
            "lookup_scope": "document",
            "reuse_authorized": True,
            "reference_resolution_only": False,
            "instances": [],
        }
        for scope in ("central", "document"):
            self.assertEqual(
                empty_existing_check_probe_failures(
                    artifact_name="synthetic_creation_checks.py",
                    tool_name="check_existing_Alpha",
                    lookup_scope=scope,
                    payload=reject,
                ),
                [],
            )
            failures = empty_existing_check_probe_failures(
                artifact_name="synthetic_creation_checks.py",
                tool_name="check_existing_Alpha",
                lookup_scope=scope,
                payload=success,
            )
            self.assertTrue(failures)
            self.assertTrue(
                all("PROPOSED_ENTITY_EVIDENCE_REQUIRED" in item for item in failures)
            )

        scoped_ok = empty_existing_check_probe_failures(
            artifact_name="synthetic_creation_checks.py",
            tool_name="check_existing_Beta",
            lookup_scope="scoped",
            payload={
                "lookup_scope": "scoped",
                "reuse_authorized": False,
                "reference_resolution_only": True,
                "instances": [],
            },
            expected_reuse_authorized=False,
            expected_reference_resolution_only=True,
        )
        self.assertEqual(scoped_ok, [])

        source = Path(
            empty_existing_check_probe_failures.__code__.co_filename
        ).read_text(encoding="utf-8")
        start = source.index("def empty_existing_check_probe_failures")
        end = source.index("\ndef ", start + 1)
        helper = source[start:end]
        for forbidden in (
            "Document",
            "Material",
            "Supplier",
            "LabEquipment",
            "ontosynthesis",
        ):
            self.assertNotIn(forbidden, helper)

    def test_ordered_violation_schema_diagnoses_violation_code_alias(self) -> None:
        codes, errors = _ordered_violation_code_report(
            {
                "status": "rejected",
                "violations": [
                    {
                        "violation_code": "missing_order",
                        "member": "urn:test:member",
                    }
                ],
            }
        )

        self.assertEqual(codes, set())
        self.assertEqual(len(errors), 1)
        self.assertIn("`violation_code`", errors[0])
        self.assertIn("required key `code`", errors[0])

    def test_ordered_violation_schema_accepts_exact_code_key(self) -> None:
        codes, errors = _ordered_violation_code_report(
            {
                "status": "rejected",
                "violations": [
                    {
                        "code": "missing_order",
                        "member": "urn:test:member",
                    }
                ],
            }
        )

        self.assertEqual(codes, {"missing_order"})
        self.assertEqual(errors, [])

    def test_stage_prompt_validation_isolated_to_active_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts = root / "prompts" / "onto"
            scripts = root / "scripts" / "onto"
            prompts.mkdir(parents=True)
            scripts.mkdir(parents=True)
            active = prompts / "KG_BUILDING_ITER_2.md"
            active.write_text(
                "{doi}\n{entity_label}\n{entity_uri}\n{iteration_hints}\nUse MCP tools.",
                encoding="utf-8",
            )
            (prompts / "EXTRACTION_ITER_3.md").write_text(
                "Use Crystallize.",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                output_root=str(root),
                prompts_dir=str(prompts),
                scripts_dir=str(scripts),
                report_path=str(root / "report.json"),
                ontology=SimpleNamespace(name="onto"),
                contract={},
                parsed={
                    "classes": {"Crystallize": {"comment": "Never use this."}},
                    "properties": {},
                },
            )

            report = build_validation_report(
                context,
                write_report=False,
                active_artifacts=[
                    active.relative_to(root).as_posix(),
                ],
            )

            self.assertTrue(report["stage_ok"], report["failures"])
            self.assertFalse(
                any("Crystallize" in failure for failure in report["failures"])
            )

    def test_scripts_only_package_skips_empty_prompt_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts = root / "prompts" / "onto"
            scripts = root / "scripts" / "onto"
            prompts.mkdir(parents=True)
            scripts.mkdir(parents=True)
            (scripts / "main.py").write_text("mcp = None\n", encoding="utf-8")
            (prompts / "EXTRACTION_ITER_1.md").write_text("", encoding="utf-8")
            (prompts / "KG_BUILDING_ITER_1.md").write_text("", encoding="utf-8")
            context = SimpleNamespace(
                output_root=str(root),
                prompts_dir=str(prompts),
                scripts_dir=str(scripts),
                report_path=str(root / "report.json"),
                ontology=SimpleNamespace(name="onto", role="core"),
                contract={"ontology_name": "onto"},
                parsed={"classes": {}, "properties": {}},
            )

            scripts_only = build_validation_report(
                context,
                write_report=False,
                include_prompt_checks=False,
            )
            self.assertFalse(
                any(
                    "missing runtime binding" in failure
                    for failure in scripts_only["failures"]
                ),
                scripts_only["failures"],
            )
            binding_statuses = {
                item["status"]
                for item in scripts_only["observations"]
                if item["check_id"] == "generation.prompt_runtime_binding"
            }
            self.assertEqual(binding_statuses, {"blocked"})

            full = build_validation_report(context, write_report=False)
            self.assertTrue(
                any(
                    "missing runtime binding" in failure
                    for failure in full["failures"]
                ),
                full["failures"],
            )

    def test_stage_prompt_mechanical_validation_defers_tbox_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts = root / "prompts" / "onto"
            scripts = root / "scripts" / "onto"
            prompts.mkdir(parents=True)
            scripts.mkdir(parents=True)
            active = prompts / "EXTRACTION_ITER_3.md"
            active.write_text(
                "{paper_content}\n{entity_label}\n{entity_uri}\nUse Crystallize.",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                output_root=str(root),
                prompts_dir=str(prompts),
                scripts_dir=str(scripts),
                report_path=str(root / "report.json"),
                ontology=SimpleNamespace(name="onto"),
                contract={},
                parsed={
                    "classes": {"Crystallize": {"comment": "Never use this."}},
                    "properties": {},
                },
            )

            report = build_validation_report(
                context,
                write_report=False,
                active_artifacts=[
                    active.relative_to(root).as_posix(),
                ],
            )

            self.assertTrue(report["stage_ok"], report["failures"])

    def test_prompt_tbox_fidelity_accepts_explicit_forbidden_class_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prompts = Path(tmp)
            (prompts / "KG_BUILDING_ITER_3.md").write_text(
                "Never create Crystallize. Never use Crystallize.",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                prompts_dir=str(prompts),
                parsed={
                    "classes": {"Crystallize": {"comment": "Never use this."}},
                    "properties": {},
                },
            )

            failures, warnings = _prompt_tbox_fidelity_report(context)

        self.assertEqual(failures, [])
        self.assertEqual(warnings, [])

    def test_stage_prompt_contract_enforces_runtime_bindings_before_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts = root / "prompts" / "onto"
            scripts = root / "scripts" / "onto"
            prompts.mkdir(parents=True)
            scripts.mkdir(parents=True)
            prompt = prompts / "KG_BUILDING_ITER_2.md"
            prompt.write_text(
                "{iteration_hints}\n{entity_label}\n{entity_uri}\nUse MCP tools.",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                output_root=str(root),
                prompts_dir=str(prompts),
                scripts_dir=str(scripts),
                ontology=SimpleNamespace(name="onto"),
                contract={},
            )
            relative = prompt.relative_to(root).as_posix()

            failures, _, _ = _stage_artifact_contract_report(context, [relative])

            self.assertTrue(any("{doi}" in failure for failure in failures))

    def test_iter1_kg_prompt_prose_is_not_keyword_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prompts = Path(tmp) / "prompts"
            prompts.mkdir()
            path = prompts / "KG_BUILDING_ITER_1.md"
            context = SimpleNamespace(
                prompts_dir=str(prompts),
                contract={"top_entity": {"class_local": "Record"}},
            )

            path.write_text(
                "Use tbox_scope.top_entity.creator_tool for <root-class-from-active-T-Box>.",
                encoding="utf-8",
            )
            failures, _ = _iter1_kg_prompt_execution_contract_report(context)
            self.assertEqual(failures, [])

            path.write_text(
                "Use the active-T-Box-derived `create_Record` tool exactly.",
                encoding="utf-8",
            )
            failures, _ = _iter1_kg_prompt_execution_contract_report(context)
            self.assertEqual(failures, [])

    def test_iter1_missing_creator_is_not_inferred_from_prompt_prose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prompts = Path(tmp) / "prompts"
            prompts.mkdir()
            (prompts / "KG_BUILDING_ITER_1.md").write_text(
                "Use the configured top-entity creator.", encoding="utf-8"
            )
            context = SimpleNamespace(
                prompts_dir=str(prompts),
                contract={"top_entity": {}},
            )

            failures, _ = _iter1_kg_prompt_execution_contract_report(context)

            self.assertEqual(failures, [])

    def test_kg_owned_scope_prose_is_not_regex_gated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prompts = Path(tmp) / "prompts"
            prompts.mkdir()
            path = prompts / "KG_BUILDING_ITER_2.md"
            context = SimpleNamespace(
                prompts_dir=str(prompts),
                iteration_blueprint={
                    "iterations": [
                        {
                            "iteration_number": 2,
                            "semantic_scope": {
                                "classes": [
                                    {"local": "ChemicalInput"},
                                    {"local": "ChemicalOutput"},
                                ],
                                "object_properties": [
                                    {"local": "hasChemicalInput"},
                                ],
                            },
                        }
                    ]
                },
            )
            path.write_text(
                "- Iteration-owned classes: [ChemicalOutput]\n"
                "- Iteration-owned object_properties: [hasChemicalInput]\n"
                "- Do not broaden into ChemicalInput.\n",
                encoding="utf-8",
            )
            failures, _ = _kg_iteration_owned_scope_report(context)
            self.assertEqual(failures, [])

            path.write_text(
                "- Iteration-owned classes: [ChemicalInput, ChemicalOutput]\n"
                "- Iteration-owned object_properties: [hasChemicalInput]\n",
                encoding="utf-8",
            )
            failures, _ = _kg_iteration_owned_scope_report(context)
            self.assertEqual(failures, [])

            path.write_text(
                "- Iteration-owned classes: [ChemicalInput, ChemicalOutput]\n"
                "- Iteration-owned object_properties: [hasChemicalInput]\n"
                "- Never create ChemicalInput for washing-solvent evidence.\n"
                "- Do not broaden into other ontology tasks.\n",
                encoding="utf-8",
            )
            failures, _ = _kg_iteration_owned_scope_report(context)
            self.assertEqual(failures, [])

            path.write_text(
                "- Iteration-owned classes: [ChemicalInput, ChemicalOutput]\n"
                "- Iteration-owned object_properties: [hasChemicalInput]\n"
                "- Do not create ChemicalInput instances in this iteration.\n",
                encoding="utf-8",
            )
            failures, _ = _kg_iteration_owned_scope_report(context)
            self.assertEqual(failures, [])

    def test_runtime_hygiene_rejects_vararg_mcp_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "scripts" / "onto"
            scripts.mkdir(parents=True)
            (scripts / "main.py").write_text(
                "def init_memory():\n    return {}\n\n"
                "def export_memory(*args, **kwargs):\n    return {}\n",
                encoding="utf-8",
            )
            context = SimpleNamespace(scripts_dir=str(scripts))

            failures, _, obligations = _runtime_graph_hygiene_report(context)

        self.assertTrue(any("FastMCP" in failure for failure in failures))
        self.assertEqual(len(obligations), len(failures))

    def test_runtime_hygiene_rejects_aggregate_materializer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "scripts" / "onto"
            scripts.mkdir(parents=True)
            (scripts / "main.py").write_text(
                "def init_memory(doi, top_level_entity_name):\n    return {}\n\n"
                "def export_memory():\n    return {}\n\n"
                "def materialize_hints(doi, top_level_entity_name, entity_label, hints_json):\n"
                "    return {}\n",
                encoding="utf-8",
            )
            context = SimpleNamespace(scripts_dir=str(scripts))

            failures, _, obligations = _runtime_graph_hygiene_report(context)

        self.assertTrue(any("forbidden" in failure for failure in failures))
        self.assertEqual(len(obligations), len(failures))

    def test_observation_schema_is_stable_and_preserves_failure_evidence(self) -> None:
        observation = build_validation_observation(
            check_id="generation.syntax",
            subject_key="medical",
            stage="syntax",
            failures=["main.py: syntax error"],
            observed_artifacts=["scripts/medical/main.py"],
        )

        self.assertEqual(
            set(observation),
            {
                "check_id",
                "subject_key",
                "stage",
                "status",
                "observed_artifacts",
                "blocked_by",
                "evidence",
                "message",
            },
        )
        self.assertEqual(observation["status"], "fail")
        self.assertEqual(
            observation["evidence"]["failures"], ["main.py: syntax error"]
        )

    def test_full_validation_splits_failures_into_atomic_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts" / "onto"
            prompts = root / "prompts" / "onto"
            scripts.mkdir(parents=True)
            prompts.mkdir(parents=True)
            (scripts / "broken_a.py").write_text("def broken(:\n", encoding="utf-8")
            (scripts / "broken_b.py").write_text("if True print('x')\n", encoding="utf-8")
            context = SimpleNamespace(
                scripts_dir=str(scripts),
                prompts_dir=str(prompts),
                output_root=str(root),
                report_path=str(root / "report.json"),
                contract={"ontology_name": "onto"},
                ontology=SimpleNamespace(name="onto", role="main"),
                parsed={"properties": {}, "classes": {}},
            )

            report = build_validation_report(context, write_report=False)
            syntax_failures = [
                item
                for item in report["observations"]
                if item["check_id"] == "generation.syntax"
                and item["status"] == "fail"
                and item["subject_key"] != "onto"
            ]

            self.assertEqual(len(syntax_failures), 2)
            self.assertEqual(
                len({item["subject_key"] for item in syntax_failures}), 2
            )
            self.assertTrue(
                all(len(item["evidence"]["failures"]) == 1 for item in syntax_failures)
            )
            self.assertEqual(
                {item["subject_key"] for item in syntax_failures},
                {
                    "onto/artifact:broken_a.py#python-syntax",
                    "onto/artifact:broken_b.py#python-syntax",
                },
            )
            self.assertTrue(
                all("obligation-" not in item["subject_key"] for item in syntax_failures)
            )

    def test_generated_script_slice_validates_for_both_target_ontologies(self) -> None:
        contexts = [
            build_agentic_generation_context(
                ontology_name="medical",
                output_root=Path("tmp/agentic_generation/test_validation"),
                write_files=True,
            ),
            build_agentic_generation_context(
                ontology_name="ontosynthesis",
                output_root=Path("tmp/agentic_generation/test_validation"),
                write_files=True,
            ),
        ]
        contracts = [ctx.contract for ctx in contexts]

        for ctx in contexts:
            with self.subTest(ontology=ctx.ontology.name):
                generate_deterministic_script_slice(ctx)
                foreign = [bundle for bundle in contracts if bundle.get("ontology_name") != ctx.ontology.name]
                report = build_validation_report(ctx, foreign_contracts=foreign, write_report=True)
                self.assertTrue(report["ok"], report["failures"])
                self.assertTrue(Path(ctx.report_path).is_file())
                self.assertTrue(
                    (Path(ctx.scripts_dir) / "_fixed_rdf_runtime.py").is_file()
                )

    def test_medical_deterministic_prompts_pass_csv_roundtrip_validation(self) -> None:
        root = Path("tmp/agentic_generation/test_medical_csv_roundtrip")
        ctx = build_agentic_generation_context(
            ontology_name="medical",
            output_root=root,
            write_files=True,
        )
        generate_deterministic_prompt_slice(ctx)
        generate_deterministic_script_slice(ctx)
        report = build_validation_report(ctx, foreign_contracts=[], write_report=False)
        self.assertTrue(report["ok"], report["failures"])

    def test_prompt_validation_does_not_require_ontology_named_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prompts = Path(tmp)
            (prompts / "EXTRACTION_ITER_2.md").write_text(
                "Use only evidence and the active T-Box.", encoding="utf-8"
            )
            context = SimpleNamespace(
                prompts_dir=str(prompts),
                parsed={"classes": {}, "properties": {}},
            )
            failures, warnings = _prompt_tbox_fidelity_report(context)
        self.assertEqual(failures, [])
        self.assertEqual(warnings, [])

    def test_init_memory_ast_contract_accepts_equivalent_variable_names(self) -> None:
        tree = ast.parse(
            "def init_memory(doi, top_level_entity_name):\n"
            "    persisted, export = rdf_runtime.scoped_memory_paths(doi, top_level_entity_name)\n"
            "    if persisted.is_file():\n"
            "        rdf_runtime.initialize_retained_graph(source_path=str(persisted))\n"
        )
        evidence = _init_memory_ast_evidence(tree.body[0])
        self.assertEqual(evidence["path_variables"], ["persisted"])
        self.assertTrue(evidence["guarded_initializers"])

    def test_prompt_tbox_fidelity_defers_forbidden_derivation_to_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prompts = Path(tmp)
            (prompts / "EXTRACTION_ITER_2.md").write_text(
                "For ReportedYield, choose the midpoint and convert it to percent.",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                prompts_dir=str(prompts),
                parsed={
                    "classes": {
                        "ReportedYield": {
                            "comment": "Record exactly as written. Do not calculate or derive."
                        }
                    },
                    "properties": {},
                },
            )
            failures, _ = _prompt_tbox_fidelity_report(context)
            self.assertEqual(failures, [])

    def test_relationship_binding_gate_is_helper_name_independent(self) -> None:
        tree = ast.parse(
            "def add_hasTarget(subject_iri, object_iri):\n"
            "    return arbitrary_adapter('https://example.test/hasTarget', "
            "subject_iri, object_iri)\n"
        )
        evidence = _relationship_binding_evidence(tree.body[0])
        self.assertEqual(evidence["call_count"], 1)
        self.assertEqual(
            evidence["bound_iris"], ["https://example.test/hasTarget"]
        )

    def test_relationship_binding_gate_tracks_local_capability_binding(self) -> None:
        tree = ast.parse(
            "def add_hasTarget(subject_iri, object_iri):\n"
            "    callable_local = registry['https://example.test/hasTarget']\n"
            "    result = callable_local(subject_iri, object_iri)\n"
            "    return result\n"
        )
        evidence = _relationship_binding_evidence(tree.body[0])
        self.assertEqual(evidence["call_count"], 1)
        self.assertEqual(
            evidence["bound_iris"], ["https://example.test/hasTarget"]
        )

    def test_relationship_binding_gate_excludes_input_validation_call(self) -> None:
        tree = ast.parse(
            "def add_hasTarget(subject_iri, object_iri):\n"
            "    error = validate_iris(subject_iri, object_iri)\n"
            "    if error:\n"
            "        return error\n"
            "    writer = registry['https://example.test/hasTarget']\n"
            "    return writer(subject_iri, object_iri)\n"
        )
        evidence = _relationship_binding_evidence(tree.body[0], module=tree)
        self.assertEqual(evidence["call_count"], 1)
        self.assertEqual(
            evidence["bound_iris"], ["https://example.test/hasTarget"]
        )

    def test_relationship_binding_gate_resolves_module_constant_and_alias(self) -> None:
        tree = ast.parse(
            "PREDICATE = 'https://example.test/hasTarget'\n"
            "PREDICATE_ALIAS = PREDICATE\n"
            "def add_hasTarget(subject_iri, object_iri):\n"
            "    return dispatch(PREDICATE_ALIAS, subject_iri, object_iri)\n"
        )
        evidence = _relationship_binding_evidence(tree.body[2], module=tree)
        self.assertEqual(evidence["call_count"], 1)
        self.assertEqual(evidence["binding_status"], "proven")
        self.assertEqual(
            evidence["bound_iris"], ["https://example.test/hasTarget"]
        )

    def test_relationship_binding_gate_marks_dynamic_binding_unknown(self) -> None:
        tree = ast.parse(
            "def add_hasTarget(subject_iri, object_iri):\n"
            "    predicate = lookup_predicate()\n"
            "    return dispatch(predicate, subject_iri, object_iri)\n"
        )
        evidence = _relationship_binding_evidence(tree.body[0], module=tree)
        self.assertEqual(evidence["call_count"], 1)
        self.assertEqual(evidence["binding_status"], "unknown")
        self.assertEqual(evidence["bound_iris"], [])

    def test_relationship_binding_gate_proves_generated_module_constant_pattern(
        self,
    ) -> None:
        tree = ast.parse(
            "RELATIONSHIP_WRITERS = runtime.package_relationship_capabilities()\n"
            "HAS_TARGET = 'https://example.test/hasTarget'\n"
            "def _write(predicate_iri, subject_iri, object_iri):\n"
            "    writer = RELATIONSHIP_WRITERS[predicate_iri]\n"
            "    return writer(subject_iri, object_iri)\n"
            "def add_hasTarget(subject_iri, object_iri):\n"
            "    return _write(HAS_TARGET, subject_iri, object_iri)\n"
        )
        evidence = _relationship_binding_evidence(tree.body[3], module=tree)
        self.assertEqual(evidence["call_count"], 1)
        self.assertEqual(evidence["binding_status"], "proven")
        self.assertEqual(
            evidence["bound_iris"], ["https://example.test/hasTarget"]
        )

    def test_relationship_binding_gate_proves_local_constant_alias(self) -> None:
        tree = ast.parse(
            "PREDICATE = 'https://example.test/hasTarget'\n"
            "def add_hasTarget(subject_iri, object_iri):\n"
            "    local_predicate = PREDICATE\n"
            "    return dispatch(local_predicate, subject_iri, object_iri)\n"
        )
        evidence = _relationship_binding_evidence(tree.body[1], module=tree)
        self.assertEqual(evidence["binding_status"], "proven")
        self.assertEqual(
            evidence["bound_iris"], ["https://example.test/hasTarget"]
        )

    def test_stage_validation_blocks_incomplete_downstream_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts" / "onto"
            prompts = root / "prompts" / "onto"
            scripts.mkdir(parents=True)
            prompts.mkdir(parents=True)
            main = scripts / "main.py"
            main.write_text(
                "def init_memory():\n    pass\n\n"
                "def export_memory():\n    pass\n\n"
                "def materialize_hints():\n    pass\n",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                ontology=SimpleNamespace(name="onto"),
                scripts_dir=str(scripts),
                prompts_dir=str(prompts),
                report_path=str(root / "report.json"),
                contract={"ontology_name": "onto"},
            )

            report = build_validation_report(
                context,
                write_report=False,
                active_artifacts=["scripts/onto/main.py"],
            )

            statuses = {
                item["check_id"]: item["status"]
                for item in report["observations"]
            }
            self.assertEqual(
                statuses["generation.stage_artifact_contract"], "fail"
            )
            self.assertEqual(statuses["generation.syntax"], "pass")
            self.assertEqual(statuses["generation.import_smoke"], "blocked")
            self.assertEqual(statuses["generation.contract_bundle"], "blocked")
            self.assertFalse(report["stage_ok"])

    def test_stage_checks_validation_does_not_import_incomplete_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = build_agentic_generation_context(
                ontology_name="ontosynthesis",
                output_root=root,
                write_files=True,
            )
            generate_deterministic_script_slice(context)
            scripts = Path(context.scripts_dir)
            (scripts / "main.py").write_text(
                "from .module_not_generated_yet import missing\n",
                encoding="utf-8",
            )
            checks = scripts / "ontosynthesis_creation_checks.py"

            report = build_validation_report(
                context,
                write_report=False,
                active_artifacts=[
                    checks.relative_to(root).as_posix(),
                ],
            )

            self.assertFalse(
                any(
                    "module_not_generated_yet" in failure
                    or "main.py import failed" in failure
                    for failure in report["failures"]
                ),
                report["failures"],
            )

    def test_stage_syntax_ignores_nonactive_broken_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = build_agentic_generation_context(
                ontology_name="ontosynthesis",
                output_root=root,
                write_files=True,
            )
            generate_deterministic_script_slice(context)
            scripts = Path(context.scripts_dir)
            (scripts / "main.py").write_text("def broken(:\n", encoding="utf-8")
            active = scripts / "ontosynthesis_creation_entities.py"

            report = build_validation_report(
                context,
                write_report=False,
                active_artifacts=[active.relative_to(root).as_posix()],
            )

            self.assertFalse(
                any(
                    failure.startswith("main.py: syntax error")
                    for failure in report["failures"]
                ),
                report["failures"],
            )

    def test_stage_main_does_not_run_full_package_surface_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = build_agentic_generation_context(
                ontology_name="ontosynthesis",
                output_root=root,
                write_files=True,
            )
            generate_deterministic_script_slice(context)
            main = Path(context.scripts_dir) / "main.py"
            with patch(
                "src.agents.scripts_and_prompts_generation."
                "agentic_generation_validation._expected_tool_surface_report",
                side_effect=AssertionError("full surface probe must be deferred"),
            ):
                _stage_artifact_contract_report(
                    context, [main.relative_to(root).as_posix()]
                )

    def test_prompt_contract_tolerates_incomplete_generated_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = build_agentic_generation_context(
                ontology_name="ontosynthesis",
                output_root=root,
                write_files=True,
            )
            scripts = Path(context.scripts_dir)
            (scripts / "main.py").write_text(
                "from .module_not_generated_yet import missing\n",
                encoding="utf-8",
            )

            contract = pure_llm_generation._prompt_agent_tool_contract(
                context,
                {
                    "classes": ["ChemicalSynthesis"],
                    "object_properties": [],
                    "linked_materialization_classes": [],
                },
                {},
            )

            self.assertIn("lifecycle_tools", contract)

    def test_stage_validation_rejects_incomplete_local_entity_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = build_agentic_generation_context(
                ontology_name="ontosynthesis",
                output_root=root,
                write_files=True,
            )
            path = Path(context.scripts_dir) / "ontosynthesis_creation_entities.py"
            path.write_text("def create_ChemicalSynthesis():\n    pass\n", encoding="utf-8")

            report = build_validation_report(
                context,
                write_report=False,
                active_artifacts=[
                    "scripts/ontosynthesis/ontosynthesis_creation_entities.py"
                ],
            )

            observation = next(
                item
                for item in report["observations"]
                if item["check_id"] == "generation.stage_artifact_contract"
            )
            self.assertEqual(observation["status"], "fail")
            self.assertTrue(
                any(
                    "create_" in failure
                    for failure in observation["evidence"]["failures"]
                )
            )
            self.assertIn(
                "exactly create_<class_local>",
                observation["evidence"]["entity_tool_naming_contract"],
            )
            self.assertFalse(report["stage_ok"])

    def test_stage_relationship_failure_exposes_exact_add_tool_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = build_agentic_generation_context(
                ontology_name="ontosynthesis",
                output_root=root,
                write_files=True,
            )
            path = (
                Path(context.scripts_dir)
                / "ontosynthesis_creation_relationships.py"
            )
            path.write_text("def helper():\n    pass\n", encoding="utf-8")

            report = build_validation_report(
                context,
                write_report=False,
                active_artifacts=[
                    "scripts/ontosynthesis/"
                    "ontosynthesis_creation_relationships.py"
                ],
            )

            observation = next(
                item
                for item in report["observations"]
                if item["check_id"] == "generation.stage_artifact_contract"
            )
            failures = observation["evidence"]["failures"]
            self.assertTrue(any("missing stage relationship tools" in item for item in failures))
            self.assertTrue(any("add_" in item for item in failures))
            self.assertIn(
                "exactly add_<predicate_local>",
                observation["evidence"]["relationship_tool_naming_contract"],
            )

    def test_stage_relationship_does_not_hard_reject_description_phrases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts" / "onto"
            prompts = root / "prompts" / "onto"
            scripts.mkdir(parents=True)
            prompts.mkdir(parents=True)
            path = scripts / "onto_creation_relationships.py"
            path.write_text(
                "def add_hasTarget(subject_iri: str, object_iri: str):\n"
                "    return object_iri\n",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                scripts_dir=str(scripts),
                prompts_dir=str(prompts),
                output_root=str(root),
                report_path=str(root / "report.json"),
                ontology=SimpleNamespace(name="onto", role="main"),
                parsed={
                    "classes": {"Target": {}},
                    "properties": {"hasTarget": {"kind": "object"}},
                },
                contract={
                    "ontology_name": "onto",
                    "relationship_tool_contracts": {
                        "hasTarget": {
                            "domain_iris": ["https://example.test/Source"],
                            "range_locals": ["Target"],
                            "creator_tools": ["create_Target"],
                        }
                    },
                },
            )

            report = build_validation_report(
                context,
                write_report=False,
                active_artifacts=["scripts/onto/onto_creation_relationships.py"],
            )
            observation = next(
                item
                for item in report["observations"]
                if item["check_id"] == "generation.stage_artifact_contract"
            )

            self.assertFalse(
                any(
                    "Field(description)" in failure
                    or "absolute-IRI" in failure
                    or "must mention" in failure
                    for failure in observation["evidence"]["failures"]
                )
            )

    def test_prompt_interchange_semantics_are_not_keyword_gated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts" / "onto"
            prompts = root / "prompts" / "onto"
            scripts.mkdir(parents=True)
            prompts.mkdir(parents=True)
            path = prompts / "EXTRACTION_ITER_2.md"
            path.write_text(
                "Expected JSON Shape:\n"
                "Hint Schema: canonical-class-sections.v1\n"
                '{"ExampleClass": []}\n',
                encoding="utf-8",
            )
            context = SimpleNamespace(
                scripts_dir=str(scripts),
                prompts_dir=str(prompts),
                output_root=str(root),
                report_path=str(root / "report.json"),
                ontology=SimpleNamespace(name="onto", role="main"),
                parsed={"classes": {}, "properties": {}},
                contract={"ontology_name": "onto"},
            )

            failures, warnings = _iteration_prompt_schema_contract_report(
                context, [path]
            )

            self.assertEqual(failures, [])
            self.assertEqual(warnings, [])

    def test_prompt_heading_wording_is_not_a_hard_contract(self) -> None:
        root = Path("tmp/agentic_generation/test_medical_mutual_exclusion_negative")
        ctx = build_agentic_generation_context(
            ontology_name="medical",
            output_root=root,
            write_files=True,
        )
        generate_deterministic_prompt_slice(ctx)
        generate_deterministic_script_slice(ctx)
        path = Path(ctx.prompts_dir) / "EXTRACTION_ITER_2.md"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(
                "Mutually Exclusive Property Contract:",
                "REMOVED MUTUAL EXCLUSION CONTRACT:",
            ),
            encoding="utf-8",
        )
        report = build_validation_report(ctx, foreign_contracts=[], write_report=False)
        self.assertTrue(report["ok"], report["failures"])

    def test_foreign_symbol_regex_does_not_scan_prompt_prose(self) -> None:
        root = Path("tmp/agentic_generation/test_prompt_residue")
        medical = build_agentic_generation_context(
            ontology_name="medical",
            output_root=root,
            write_files=True,
        )
        ontosynthesis = build_agentic_generation_context(
            ontology_name="ontosynthesis",
            output_root=root,
            write_files=True,
        )
        prompt_dir = Path(medical.prompts_dir)
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = prompt_dir / "KG_BUILDING_ITER_2.md"
        medical_symbols = set(medical.contract.get("ontology_symbol_locals") or [])
        foreign_symbol = next(
            symbol
            for symbol in sorted(ontosynthesis.contract.get("ontology_symbol_locals") or [])
            if symbol not in medical_symbols and len(str(symbol)) >= 4
        )
        prompt_path.write_text(
            f"This prompt accidentally mentions {foreign_symbol} from another ontology.",
            encoding="utf-8",
        )

        empty_scripts = root / "empty_scripts"
        empty_scripts.mkdir(parents=True, exist_ok=True)
        scan_context = SimpleNamespace(
            contract=medical.contract,
            scripts_dir=str(empty_scripts),
        )
        failures, _ = _foreign_symbol_report(
            scan_context,
            foreign_contracts=[ontosynthesis.contract],
        )
        self.assertEqual(failures, [])

    def test_schema_marker_removal_is_owned_by_semantic_reviewer(self) -> None:
        root = Path("tmp/agentic_generation/test_canonical_marker")
        ctx = build_agentic_generation_context(
            ontology_name="ontosynthesis",
            output_root=root,
            write_files=True,
        )
        generate_deterministic_prompt_slice(ctx)
        # choose a deterministic iteration prompt if present
        path = Path(ctx.prompts_dir) / "EXTRACTION_ITER_2.md"
        self.assertTrue(path.is_file(), f"missing iteration prompt: {path}")
        text = path.read_text(encoding="utf-8")
        self.assertIn("Hint Schema: ref-entity-relations.v1", text)
        # Mechanical validation must not infer prompt semantics from a marker.
        path.write_text(
            text.replace("Hint Schema: ref-entity-relations.v1", "REMOVED MARKER TEST"),
            encoding="utf-8",
        )
        failures, warnings = _iteration_prompt_schema_contract_report(ctx, [path])
        self.assertEqual(failures, [])
        self.assertEqual(warnings, [])

    def test_relationship_tools_publish_parameter_json_schema_description(self) -> None:
        root = Path("tmp/agentic_generation/test_relationship_param_desc")
        ctx = build_agentic_generation_context(
            ontology_name="ontosynthesis",
            output_root=root,
            write_files=True,
        )
        generate_deterministic_script_slice(ctx)
        rel_path = Path(ctx.scripts_dir) / "ontosynthesis_creation_relationships.py"
        self.assertTrue(rel_path.is_file(), f"missing relationships module: {rel_path}")
        src = rel_path.read_text(encoding="utf-8")
        self.assertIn("Annotated[", src)
        self.assertIn("Field(description=", src)
        self.assertIn("never a label/name/literal/plain text", src)

    def test_om2_ast_gate_base_no_import_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts" / "onto"
            prompts = root / "prompts" / "onto"
            scripts.mkdir(parents=True)
            prompts.mkdir(parents=True)
            (scripts / "_fixed_rdf_runtime.py").write_text("", encoding="utf-8")
            path = scripts / "onto_creation_base.py"
            path.write_text(
                "from . import _fixed_rdf_runtime as rdf_runtime\n"
                "__all__ = ['rdf_runtime']\n",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                ontology=SimpleNamespace(name="onto"),
                scripts_dir=str(scripts),
                prompts_dir=str(prompts),
                report_path=str(root / "report.json"),
                contract={"ontology_name": "onto", "om2_quantity_properties": ["amount"]},
            )
            report = build_validation_report(
                context,
                write_report=False,
                active_artifacts=[f"scripts/onto/{path.name}"],
            )
            observation = next(
                item
                for item in report["observations"]
                if item["check_id"] == "generation.stage_artifact_contract"
            )
            self.assertEqual(observation["status"], "pass")

    def test_om2_ast_gate_absolute_import_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts" / "onto"
            prompts = root / "prompts" / "onto"
            scripts.mkdir(parents=True)
            prompts.mkdir(parents=True)
            (scripts / "_fixed_rdf_runtime.py").write_text("", encoding="utf-8")
            path = scripts / "onto_creation_base.py"
            path.write_text(
                "from . import _fixed_rdf_runtime as rdf_runtime\n"
                "from fixed_om2_runtime import om2_as_quantity\n"
                "__all__ = ['rdf_runtime']\n",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                ontology=SimpleNamespace(name="onto"),
                scripts_dir=str(scripts),
                prompts_dir=str(prompts),
                report_path=str(root / "report.json"),
                contract={"ontology_name": "onto", "om2_quantity_properties": ["amount"]},
            )
            report = build_validation_report(
                context,
                write_report=False,
                active_artifacts=[f"scripts/onto/{path.name}"],
            )
            observation = next(
                item
                for item in report["observations"]
                if item["check_id"] == "generation.stage_artifact_contract"
            )
            self.assertEqual(observation["status"], "fail")
            failures_text = " ".join(observation.get("evidence", {}).get("failures", []))
            self.assertIn("fixed_om2_runtime", failures_text)

    def test_om2_ast_gate_relative_import_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts" / "onto"
            prompts = root / "prompts" / "onto"
            scripts.mkdir(parents=True)
            prompts.mkdir(parents=True)
            (scripts / "_fixed_rdf_runtime.py").write_text("", encoding="utf-8")
            (scripts / "_fixed_om2_runtime.py").write_text(
                "om2_as_quantity = object()\n",
                encoding="utf-8",
            )
            path = scripts / "onto_creation_base.py"
            path.write_text(
                "from . import _fixed_rdf_runtime as rdf_runtime\n"
                "from ._fixed_om2_runtime import om2_as_quantity\n"
                "__all__ = ['rdf_runtime']\n",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                ontology=SimpleNamespace(name="onto"),
                scripts_dir=str(scripts),
                prompts_dir=str(prompts),
                report_path=str(root / "report.json"),
                contract={"ontology_name": "onto", "om2_quantity_properties": ["amount"]},
            )
            report = build_validation_report(
                context,
                write_report=False,
                active_artifacts=[f"scripts/onto/{path.name}"],
            )
            observation = next(
                item
                for item in report["observations"]
                if item["check_id"] == "generation.stage_artifact_contract"
            )
            self.assertEqual(observation["status"], "pass")


if __name__ == "__main__":
    unittest.main()
