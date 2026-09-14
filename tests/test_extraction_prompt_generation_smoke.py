"""No-LLM smoke tests for extraction prompt generation.

These tests exercise config load, T-Box parse, SPARQL compile, MCP-set load,
deterministic ownership planning, and contract compile. They must never call
an LLM. `--stage context` / `--stage all` are not smokes: those invoke GPT-5.
"""

from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from models.MCPConfig import (
    load_mcp_set_document,
    load_mcp_set_extraction_validation,
    load_mcp_set_tool_purposes,
)
from src.extraction_prompt_generation.cli import _run_one_ontology, build_parser
from src.extraction_prompt_generation.pipeline import (
    run_agentic_generation_experiment,
)
from src.extraction_prompt_generation.compile.artifact_compiler import (
    _legacy_adapter,
    _runtime_blueprint,
    _tbox_bundle_contract,
)
from src.extraction_prompt_generation.compile.context import (
    build_agentic_generation_context,
)
from src.extraction_prompt_generation.compile.enrichment_sparql import (
    compile_enrichment_target_sparql,
    property_iri_lookup,
    resolve_declared_path_iris,
    validate_enrichment_target_declaration,
)
from src.extraction_prompt_generation.compile.iteration_plan import (
    compile_iteration_plan,
)
from src.extraction_prompt_generation.compile.reuse_judgment import (
    JUDGMENT_SCHEMA,
    assemble_stable_reuse_policy,
    class_inventory_from_ttl,
    compile_policy_from_judgment,
    generate_reuse_policy,
    validate_judgment,
)
from src.extraction_prompt_generation.compile.reuse_policy import load_reuse_policy
from src.extraction_prompt_generation.pipeline.runtime_support import (
    write_fixed_om2_runtime,
)
from src.extraction_prompt_generation.config.domain_config import (
    load_domain_generation_config,
)
from src.extraction_prompt_generation.config.namespace import (
    load_namespace_config,
)
from src.extraction_prompt_generation.generate.extraction_prompts.contracts.role_and_guidance import (
    _prompt_generation_guidance,
    _prompt_role_contract,
    _semantic_text_natural_ledger_rules,
)
from src.extraction_prompt_generation.pipeline.plan import _iteration_plan
from src.extraction_prompt_generation.runtime_support.om2 import (
    OM2,
    OM2_UNIT_MAP,
    parse_om2_quantity_label,
    resolve_om2_unit,
    resolve_qualitative_quantity_preset,
)
from src.extraction_prompt_generation.runtime_support.rdf.constants import (
    instance_base_iri,
)
from src.extraction_prompt_generation.paths import (
    available_ontologies,
    default_domain_config_path,
    repository_root,
    resolve_domain_config_path,
)
from src.extraction_prompt_generation.planning.semantic_planner import (
    plan_domain_semantics,
)
from src.extraction_prompt_generation.tbox.parser import parse_ontology_ttl
from src.extraction_prompt_generation.validate.creator_atomicity import (
    creator_call_recipe,
    parameter_binding_evidence,
    resolve_ordering_parameter_name,
)


EXPECTED_ONTOLOGIES = ("medical", "ontomops", "ontospecies", "ontosynthesis")
SMOKE_ROOTS = {
    "ontosynthesis": {
        "class_local": "ChemicalSynthesis",
        "rationale": "Smoke-selected primary synthesis root.",
        "evidence": ["ChemicalSynthesis", "hasChemicalOutput"],
    },
    "medical": {
        "class_local": "MedicalCase",
        "rationale": "Smoke-selected medical case root.",
        "evidence": ["MedicalCase", "hasPatientInfo"],
    },
    "ontomops": {
        "class_local": "MetalOrganicPolyhedron",
        "rationale": "Smoke-selected extension focus.",
        "evidence": ["MetalOrganicPolyhedron", "hasChemicalBuildingUnit"],
    },
    "ontospecies": {
        "class_local": "Species",
        "rationale": "Smoke-selected extension focus.",
        "evidence": ["Species"],
    },
}


def _block_llm(*args, **kwargs):
    raise AssertionError(
        "LLM invoked during no-LLM smoke: "
        f"args={args[:2]!r} kwargs_keys={sorted(kwargs)}"
    )


def _load_config(ontology: str):
    return load_domain_generation_config(
        resolve_domain_config_path(ontology_name=ontology),
        repository_root=repository_root(),
    )


class ExtractionPromptGenerationSmoke(unittest.TestCase):
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
            patch(
                "src.extraction_prompt_generation.compile.tbox_reusability_experiment.invoke_json",
                side_effect=_block_llm,
            ),
            patch(
                "src.extraction_prompt_generation.compile.reuse_pair_judge.invoke_json",
                side_effect=_block_llm,
            ),
            patch(
                "src.extraction_prompt_generation.generate.prompt_semantic_review.invoke_json",
                side_effect=_block_llm,
            ),
        ]
        for item in patches:
            self.addCleanup(item.stop)
            item.start()

    def test_known_ontologies_and_cli_parser(self) -> None:
        self.assertEqual(tuple(available_ontologies()), EXPECTED_ONTOLOGIES)
        parser = build_parser()
        args = parser.parse_args(["ontosynthesis", "--stage", "context", "--workers", "2"])
        self.assertEqual(args.ontology, "ontosynthesis")
        self.assertEqual(args.stage, "context")
        self.assertEqual(args.max_generation_workers, 2)
        reuse_args = parser.parse_args(["ontosynthesis", "--stage", "reuse"])
        self.assertEqual(reuse_args.stage, "reuse")
        pin_args = parser.parse_args(
            [
                "medical",
                "--stage",
                "all",
                "--selected-top-entity",
                "configs/meta_task/medical_top_entity.json",
            ]
        )
        self.assertEqual(
            pin_args.selected_top_entity,
            "configs/meta_task/medical_top_entity.json",
        )
        source = inspect.getsource(_run_one_ontology)
        self.assertIn("selected_top_entity=selected_top_entity", source)
        self.assertIn("run_agentic_generation_experiment", source)
        experiment_source = inspect.getsource(run_agentic_generation_experiment)
        self.assertIn("selected_top_entity=selected_top_entity", experiment_source)
        for name in EXPECTED_ONTOLOGIES:
            self.assertTrue(default_domain_config_path(name).is_file())

    def test_load_and_validate_all_domain_configs(self) -> None:
        expected_roles = {
            "ontosynthesis": ("complex_main", "complex", "main"),
            "medical": ("simple_main", "simple", "main"),
            "ontomops": ("simple_extension", "simple_semantic", "extension"),
            "ontospecies": ("simple_extension", "simple_semantic", "extension"),
        }
        for name, (execution, workflow, role) in expected_roles.items():
            config = _load_config(name)
            self.assertEqual(config.ontology_name, name)
            self.assertEqual(config.execution_profile, execution)
            self.assertEqual(config.workflow_profile, workflow)
            self.assertEqual(config.role, role)
            self.assertTrue(config.primary_tbox.is_file())
            for path in config.supporting_tboxes:
                self.assertTrue(path.is_file(), path)
            self.assertIsNone(config.reuse_policy_path)
            if role == "extension":
                self.assertEqual(
                    config.profile["slots"][0]["hint_representation"],
                    "semantic-text.v1",
                )

    def test_simple_extension_rejects_json_simple_profile(self) -> None:
        repo = repository_root()
        raw = json.loads(
            (repo / "configs" / "domains" / "ontomops.json").read_text(
                encoding="utf-8"
            )
        )
        raw["workflow_profile"] = "simple"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ontomops.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_domain_generation_config(path, repository_root=repo)
            self.assertIn("simple_semantic", str(ctx.exception))
            self.assertIn("simple_extension", str(ctx.exception))

    def test_reuse_judgment_prompt_matches_official_review_prompt(self) -> None:
        repo = repository_root()
        official = (
            repo / "configs" / "meta_task" / "gpt5_single_tbox_binary_reusability_prompt.md"
        )
        bundled = (
            repo
            / "src"
            / "extraction_prompt_generation"
            / "compile"
            / "reuse_judgment_prompt.md"
        )
        self.assertTrue(official.is_file())
        self.assertEqual(
            official.read_text(encoding="utf-8"),
            bundled.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "Explicit T-Box statements that instances are reusable",
            official.read_text(encoding="utf-8"),
        )

    def test_historical_binary_reuse_review_still_loadable(self) -> None:
        review = (
            repository_root()
            / "configs"
            / "meta_task"
            / "ontosynthesis_binary_reuse_review.json"
        )
        self.assertTrue(review.is_file())
        policy = load_reuse_policy(review)
        by_local = {
            str(item.get("class_local") or ""): item
            for item in policy.get("classes") or []
        }
        self.assertEqual(policy["source_schema_version"], "binary-class-reuse-review.v0")
        self.assertTrue(by_local["DocumentContext"]["reusable"])
        self.assertEqual(by_local["DocumentContext"]["reuse_scope"], "document")
        self.assertTrue(by_local["LabEquipment"]["reusable"])
        self.assertTrue(by_local["Equipment"]["reusable"])
        self.assertFalse(by_local["ChemicalSynthesis"]["reusable"])

    def test_write_fixed_om2_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = write_fixed_om2_runtime(Path(tmp) / "_fixed_om2_runtime.py")
            self.assertTrue(dest.is_file())
            text = dest.read_text(encoding="utf-8")
            self.assertIn("find_or_create_om2_quantity_from_label", text)
            self.assertIn("OM2_UNIT_MAP", text)
            self.assertIn("degreeCelsiusPerHour", text)
            self.assertNotIn("from .om2_compile import", text)
            self.assertNotIn("compile_om2_runtime_tables", text)
            self.assertNotIn("extraction_prompt_generation.runtime_support.om2", text)
            self.assertIn("_COMPACT_RE", text)
            self.assertIn("_UNIT_QUANTITY_CLASSES", text)

    def test_parse_all_tboxes(self) -> None:
        expected_classes = {
            "ontosynthesis": "ChemicalSynthesis",
            "ontomops": "MetalOrganicPolyhedron",
            "ontospecies": "Species",
            "medical": "MedicalCase",
        }
        for name in EXPECTED_ONTOLOGIES:
            config = _load_config(name)
            parsed = parse_ontology_ttl(str(config.primary_tbox))
            classes = parsed.get("classes") or {}
            self.assertIn(expected_classes[name], classes)
            self.assertGreater(len(classes), 1, name)
            if name == "ontosynthesis":
                self.assertNotIn("Crystallize", classes)
            inventory = class_inventory_from_ttl(
                config.primary_tbox.read_text(encoding="utf-8")
            )
            self.assertGreater(len(inventory), 1, name)

    def test_compile_extension_enrichment_sparql(self) -> None:
        ontosyn = parse_ontology_ttl(str(_load_config("ontosynthesis").primary_tbox))
        cases = {
            "ontomops": "https://www.theworldavatar.com/kg/ontomops/MetalOrganicPolyhedron",
            "ontospecies": (
                "http://www.theworldavatar.com/ontology/ontospecies/"
                "OntoSpecies.owl#Species"
            ),
        }
        for name, target_iri in cases.items():
            config = _load_config(name)
            declaration = validate_enrichment_target_declaration(
                config.runtime["enrichment_target"],
                prefix=f"{name}.runtime.enrichment_target",
            )
            parsed = parse_ontology_ttl(str(config.primary_tbox))
            path_iris = resolve_declared_path_iris(
                declaration["path"],
                lookup=property_iri_lookup(ontosyn, parsed),
            )
            query = compile_enrichment_target_sparql(
                path_iris=path_iris,
                target_class_iri=target_iri,
                root_variable=declaration["root_variable"],
                target_variable=declaration["target_variable"],
            )
            self.assertIn("SELECT DISTINCT", query)
            self.assertIn(target_iri, query)
            for iri in path_iris:
                self.assertIn(iri, query)

    def test_load_referenced_mcp_sets(self) -> None:
        seen: set[str] = set()
        for name in EXPECTED_ONTOLOGIES:
            config = _load_config(name)
            for group in config.mcp_capabilities.values():
                set_name = str((group or {}).get("set_name") or "").strip()
                tools = list((group or {}).get("tools") or [])
                if not set_name:
                    continue
                seen.add(set_name)
                document = load_mcp_set_document(set_name)
                for tool in tools:
                    self.assertIn(tool, document, f"{name}:{set_name}:{tool}")
                if set_name == "chemistry.json":
                    purposes = load_mcp_set_tool_purposes(set_name)
                    validation = load_mcp_set_extraction_validation(set_name)
                    self.assertIn("pubchem", purposes)
                    self.assertTrue(validation.get("required_executed_tool_groups"))
        self.assertTrue(
            {"chemistry.json", "extension.json", "ontology_mcps.json", "run_created_mcp.json"}
            <= seen
        )

    def test_no_llm_compile_and_injected_planning(self) -> None:
        for name in EXPECTED_ONTOLOGIES:
            with self.subTest(ontology=name):
                self._compile_and_plan(name)

    def _compile_and_plan(self, name: str) -> None:
        config = _load_config(name)
        adapter = _legacy_adapter(config, blueprint_path=None)
        bundle = _tbox_bundle_contract(config)
        self.assertEqual(bundle["schema_version"], "iri-aware-tbox-bundle.v1")
        self.assertTrue(bundle["primary"]["iri_inventory"]["class_iris"])

        with tempfile.TemporaryDirectory(prefix=f"epg-smoke-{name}-") as raw_tmp:
            tmp = Path(raw_tmp)
            adapter_path = tmp / "meta_task_adapter.json"
            adapter_path.write_text(
                json.dumps(adapter, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            context = build_agentic_generation_context(
                ontology_name=name,
                meta_task_config_path=adapter_path,
                output_root=tmp / "generated",
                write_files=False,
            )
            self.assertEqual(context.ontology.name, name)
            self.assertTrue(context.parsed.get("classes"))
            self.assertTrue(context.contract)

            role = config.role
            decisions = plan_domain_semantics(
                config=config,
                parsed=context.parsed,
                contract=context.contract,
                planning_dir=None,
                top_entity_owner="downstream" if role == "extension" else "iteration1",
                selected_root=SMOKE_ROOTS[name],
            )
            self.assertEqual(
                decisions["top_entity"]["class_local"],
                SMOKE_ROOTS[name]["class_local"],
            )
            iterations = (decisions.get("iteration_decomposition") or {}).get(
                "iterations"
            ) or []
            self.assertEqual(len(iterations), len(config.profile["slots"]))

            runtime_blueprint = _runtime_blueprint(config, decisions)
            plan = compile_iteration_plan(
                blueprint=runtime_blueprint,
                parsed=context.parsed,
                contract=context.contract,
                ontology_name=name,
                blueprint_provenance={"source": "no_llm_smoke"},
            )
            self.assertTrue(plan.get("iterations"))

    def test_reuse_judgment_validator_without_llm(self) -> None:
        class_iri = "http://example.org/A"
        tbox_sha256 = "abc"
        judgment = {
            "schema_version": JUDGMENT_SCHEMA,
            "decision_target": "pipeline_reuse_enabled",
            "tbox_sha256": tbox_sha256,
            "reusable_classes": [
                {
                    "class_iri": class_iri,
                    "confidence": "high",
                    "tbox_evidence": ["declared class"],
                    "pipeline_evidence": ["owned by a planned slot"],
                    "contextual_value_veto": {
                        "applies": False,
                        "direct_contextual_properties": [],
                        "repeated_owner_paths": [],
                        "ownership_recoverable_after_merge": True,
                        "explanation": "No contextual value.",
                    },
                    "reuse_scope": "document",
                    "match_basis": "stable identifier",
                    "false_merge_risk": "low if identifiers differ",
                }
            ],
            "non_reusable_classes": [],
        }
        validation = validate_judgment(
            judgment,
            class_inventory=[class_iri],
            tbox_sha256=tbox_sha256,
        )
        self.assertTrue(validation["ok"], validation["errors"])
        policy = compile_policy_from_judgment(
            judgment,
            tbox_sha256=tbox_sha256,
            inputs_digest="digest",
            model="gpt-5",
        )
        self.assertEqual(policy["classes"][0]["class_local"], "A")
        self.assertTrue(policy["classes"][0]["reusable"])

    def test_assemble_stable_reuse_policy_uses_one_valid_trial(self) -> None:
        judgment = {
            "schema_version": JUDGMENT_SCHEMA,
            "decision_target": "pipeline_reuse_enabled",
            "tbox_sha256": "abc",
            "reusable_classes": [
                {
                    "class_iri": "http://example.org/A",
                    "reuse_scope": "document",
                    "match_basis": "id",
                    "false_merge_risk": "low",
                    "confidence": "high",
                    "tbox_evidence": ["declared"],
                    "pipeline_evidence": ["owned"],
                    "contextual_value_veto": {
                        "applies": False,
                        "direct_contextual_properties": [],
                        "repeated_owner_paths": [],
                        "ownership_recoverable_after_merge": True,
                        "explanation": "none",
                    },
                }
            ],
            "non_reusable_classes": [],
        }
        failed = {
            "requested_trials": 10,
            "all_trials_valid": True,
            "valid_trials": 10,
            "unanimous_class_count": 0,
            "inventory_count": 1,
            "disagreement_classes": ["http://example.org/A"],
            "passed_10_of_10_gate": False,
        }
        policy = assemble_stable_reuse_policy(
            summary=failed,
            representative_judgment=judgment,
            tbox_sha256="abc",
            inputs_digest="digest",
            model="gpt-5",
            trials=10,
            representative_trial=1,
        )
        self.assertEqual(policy["derivation"]["mode"], "gpt5_first_valid_trial")
        self.assertFalse(policy["derivation"]["passed_stability_gate"])
        self.assertTrue(policy["classes"][0]["reusable"])

    def test_generate_reuse_policy_uses_first_valid_trial(self) -> None:
        class_iri = "http://example.org/A"
        judgment = {
            "schema_version": JUDGMENT_SCHEMA,
            "decision_target": "pipeline_reuse_enabled",
            "tbox_sha256": "",
            "reusable_classes": [
                {
                    "class_iri": class_iri,
                    "reuse_scope": "document",
                    "match_basis": "id",
                    "false_merge_risk": "low",
                    "confidence": "high",
                    "tbox_evidence": ["declared"],
                    "pipeline_evidence": ["owned"],
                    "contextual_value_veto": {
                        "applies": False,
                        "direct_contextual_properties": [],
                        "repeated_owner_paths": [],
                        "ownership_recoverable_after_merge": True,
                        "explanation": "none",
                    },
                }
            ],
            "non_reusable_classes": [],
        }

        def _passed_run(**kwargs):
            output_dir = Path(kwargs["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            payload = dict(judgment)
            payload["tbox_sha256"] = json.loads(
                (output_dir / "generation_inputs.json").read_text(encoding="utf-8")
            )["tbox_sha256"]
            (output_dir / "trial_1.json").write_text(
                json.dumps(
                    {
                        "trial": 1,
                        "validation": {"ok": True},
                        "parsed_response": payload,
                    }
                ),
                encoding="utf-8",
            )
            return {
                "requested_trials": 10,
                "all_trials_valid": True,
                "valid_trials": 10,
                "unanimous_class_count": 1,
                "inventory_count": 1,
                "disagreement_classes": [],
                "passed_10_of_10_gate": True,
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tbox = root / "onto.ttl"
            tbox.write_text(
                "@prefix owl: <http://www.w3.org/2002/07/owl#> .\n"
                "@prefix ex: <http://example.org/> .\n"
                "ex:A a owl:Class .\n",
                encoding="utf-8",
            )
            domain = root / "domain.json"
            domain.write_text("{}", encoding="utf-8")
            passed_out = root / "passed" / "reuse_policy.json"
            with patch(
                "src.extraction_prompt_generation.compile.tbox_reusability_experiment.run_experiment",
                side_effect=_passed_run,
            ):
                policy = generate_reuse_policy(
                    primary_tbox=tbox,
                    supporting_tboxes=(),
                    compiled_plan={"ontology": "ex", "iterations": []},
                    domain_config_path=domain,
                    output_path=passed_out,
                    model="gpt-5",
                    trials=10,
                )
            self.assertTrue(passed_out.is_file())
            self.assertEqual(
                policy["derivation"]["mode"],
                "gpt5_first_valid_trial",
            )
            self.assertEqual(policy["classes"][0]["class_iri"], class_iri)

    def test_creator_atomicity_helpers(self) -> None:
        def create_step(label: str, hasOrder: int, hasNote: str = "") -> None:
            del label, hasOrder, hasNote

        contract = {
            "ordered_member": True,
            "ordering_property_local": "hasOrder",
            "public_tool": "create_step",
            "datatype_inputs": [
                {
                    "property_local": "hasOrder",
                    "property_iri": "http://example.org/hasOrder",
                    "python_type": "int",
                    "required": True,
                },
                {
                    "property_local": "hasNote",
                    "property_iri": "http://example.org/hasNote",
                    "python_type": "str",
                    "required": False,
                },
            ],
        }
        signature = inspect.signature(create_step)
        self.assertEqual(
            resolve_ordering_parameter_name(contract, signature),
            "hasOrder",
        )
        evidence = parameter_binding_evidence(contract, signature)
        self.assertEqual(evidence["unbound_properties"], [])
        recipe = creator_call_recipe(contract, create_step, label="step-1")
        self.assertEqual(recipe["kwargs"]["hasOrder"], 1)
        self.assertEqual(recipe["kwargs"]["hasNote"], "Atomic probe value")

    def test_namespace_config_is_human_source_for_iris(self) -> None:
        payload = load_namespace_config()
        self.assertEqual(payload["schema_version"], "namespace-config.v1")
        self.assertTrue(payload["instance_base_iri"].startswith("http"))
        self.assertTrue(payload["generated_graph_iri"].startswith("http"))
        self.assertEqual(instance_base_iri(), payload["instance_base_iri"])

    def test_enrichment_default_root_variable_is_neutral(self) -> None:
        declaration = validate_enrichment_target_declaration(
            {"path": ["hasOutput"]},
            prefix="runtime.enrichment_target",
        )
        self.assertEqual(declaration["root_variable"], "root")
        query = compile_enrichment_target_sparql(
            path_iris=["http://example.org/hasOutput"],
            target_class_iri="http://example.org/Target",
        )
        self.assertIn("?root", query)
        self.assertNotIn("?synthesis", query)

    def test_tbox_parser_does_not_elevate_valuekind(self) -> None:
        parsed = parse_ontology_ttl(
            str(_load_config("medical").primary_tbox)
        )
        for spec in (parsed.get("properties") or {}).values():
            self.assertNotIn("value_kind", spec)
            self.assertNotIn("value_kinds", spec)

    def test_frozen_prompt_contract_avoids_application_class_names(self) -> None:
        contract = _prompt_role_contract(
            Path("EXTRACTION_ITER_2.md"),
            {
                "iteration_spec": {"hint_representation": "ref-entity-relations.v1"},
                "runtime_binding_contract": {"allowed_slots": ["{accumulated_hints}"]},
            },
        )
        text = "\n".join(contract.get("must") or [])
        self.assertIn("ref-entity-relations.v1", text)
        self.assertNotIn("Equipment", text)
        self.assertNotIn("Vessel", text)
        self.assertNotIn("formula", "\n".join(_semantic_text_natural_ledger_rules()))

    def test_extension_iter1_semantic_text_is_not_identity_json(self) -> None:
        contract = {
            "iteration_spec": {"hint_representation": "semantic-text.v1"},
            "runtime_binding_contract": {"allowed_slots": ["{paper_content}"]},
        }
        role = _prompt_role_contract(Path("EXTRACTION_ITER_1.md"), contract)
        role_text = "\n".join(role.get("must") or [])
        self.assertIn("SEMANTIC_HINTS_V1", role_text)
        self.assertIn("current target entity only", role_text)
        self.assertNotIn("ref-entity-relations.v1", role_text)
        guidance = _prompt_generation_guidance(
            Path("EXTRACTION_ITER_1.md"), contract
        )
        self.assertIn("SEMANTIC_HINTS_V1", guidance)
        self.assertNotIn("Require exactly one", guidance)

    def test_om2_maps_common_percent_and_source_spellings(self) -> None:
        for alias in (
            "percent",
            "mol%",
            "mol %",
            "wt%",
            "wt.%",
            "weight%",
            "vol%",
            "v/v%",
            "w/w%",
            "w/v%",
            "mass%",
            "atom%",
            "% (w/w)",
            "mole percent",
            "percent by weight",
        ):
            self.assertEqual(resolve_om2_unit(alias), OM2.percent, alias)
        self.assertEqual(resolve_om2_unit("mg"), OM2.milligram)
        self.assertEqual(resolve_om2_unit("mmol"), OM2.millimole)
        self.assertEqual(resolve_om2_unit("ppm"), OM2.partsPerMillion)
        self.assertEqual(resolve_om2_unit("M"), OM2.molePerLitre)
        self.assertEqual(resolve_om2_unit("mM"), OM2.millimolePerLitre)
        self.assertEqual(resolve_om2_unit("mm"), OM2.millimetre)
        self.assertEqual(resolve_om2_unit("mg mL-1"), OM2.milligramPerMillilitre)
        self.assertEqual(resolve_om2_unit("equiv."), OM2.equivalent)
        self.assertEqual(resolve_om2_unit("rpm"), OM2.revolutionPerMinute)
        self.assertEqual(resolve_om2_unit("sccm"), OM2.cubicCentimetrePerMinute)

    def test_om2_parses_compact_labels_and_rejects_prose(self) -> None:
        self.assertEqual(parse_om2_quantity_label("4 degC/h"), (4.0, "degc/h"))
        self.assertEqual(parse_om2_quantity_label("4 degC / h"), (4.0, "degc/h"))
        self.assertEqual(
            resolve_om2_unit(parse_om2_quantity_label("150°C")[1]), OM2.degreeCelsius
        )
        self.assertEqual(parse_om2_quantity_label("0.1 M")[1], "M")
        self.assertEqual(parse_om2_quantity_label("100 mM")[1], "mM")
        self.assertEqual(parse_om2_quantity_label("8 h", OM2.Duration), (8.0, "h"))
        with self.assertRaises(ValueError):
            parse_om2_quantity_label("slowly cooled at about 4 degC / h")
        with self.assertRaises(ValueError):
            parse_om2_quantity_label("4 degC/h", OM2.Duration)
        with self.assertRaises(ValueError):
            parse_om2_quantity_label("2 h 30 min")
        with self.assertRaises(ValueError):
            parse_om2_quantity_label("~3 h")
        self.assertIsNone(
            resolve_qualitative_quantity_preset(OM2.Temperature, "at room temperature")
        )
        self.assertIsNone(resolve_qualitative_quantity_preset(OM2.Duration, "o/n"))
        self.assertIsNone(resolve_qualitative_quantity_preset(OM2.Pressure, "in vacuo"))

    def test_generic_tbox_conditional_heuristics_remain_in_prompts(self) -> None:
        text = Path(
            "src/extraction_prompt_generation/pipeline/prompts.py"
        ).read_text(encoding="utf-8")
        self.assertIn("procedure-inheritance object field", text)
        self.assertIn("value_kind=binary_checklist", text)
        self.assertIn("identification or demographic fields", text)
        self.assertNotIn("Equipment, Vessel", text)

    def test_extension_output_paths_come_from_domain_config(self) -> None:
        from dataclasses import replace

        name = "ontomops"
        config = _load_config(name)
        adapter = _legacy_adapter(config, blueprint_path=None)
        with tempfile.TemporaryDirectory(prefix="epg-smoke-plan-") as raw_tmp:
            tmp = Path(raw_tmp)
            adapter_path = tmp / "meta_task_adapter.json"
            adapter_path.write_text(
                json.dumps(adapter, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            context = build_agentic_generation_context(
                ontology_name=name,
                meta_task_config_path=adapter_path,
                output_root=tmp / "generated",
                write_files=False,
            )
            decisions = plan_domain_semantics(
                config=config,
                parsed=context.parsed,
                contract=context.contract,
                planning_dir=None,
                top_entity_owner="downstream",
                selected_root=SMOKE_ROOTS[name],
            )
            plan = compile_iteration_plan(
                blueprint=_runtime_blueprint(config, decisions),
                parsed=context.parsed,
                contract=context.contract,
                ontology_name=name,
                blueprint_provenance={"source": "no_llm_smoke"},
            )
            runtime = _iteration_plan(replace(context, iteration_blueprint=plan))
            output_ttl = runtime["iterations"][0]["outputs"]["output_ttl"]
            output_cfg = ((adapter.get("ontologies") or {}).get("extensions") or [{}])[0]
            output_cfg = output_cfg.get("output") or {}
            expected_dir = str(output_cfg.get("dir") or "").replace(
                "{ontology_name}", name
            )
            expected_pattern = str(output_cfg.get("entity_ttl_pattern") or "")
            self.assertTrue(output_ttl.startswith(f"{expected_dir}/"))
            self.assertTrue(output_ttl.endswith(expected_pattern))


if __name__ == "__main__":
    unittest.main()
