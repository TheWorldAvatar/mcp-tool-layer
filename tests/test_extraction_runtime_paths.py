"""Compile, import, and no-LLM path/binding tests for extraction runtime."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from models.locations import repository_root
from src.extraction_runtime.artifact_root import generated_artifact_root, load_prompt
from src.extraction_runtime.cli import build_parser
from src.extraction_runtime.discovery import discover_dois
from src.extraction_runtime.agent.env import merge_mcp_server_environment
from src.extraction_runtime.domain_binding import (
    COMPLEX_MAIN_STEPS,
    EXTENSION_STEPS,
    SIMPLE_MAIN_STEPS,
    load_runtime_domain,
    pipeline_main_ontology_name,
    resolve_derivation_jobs,
    selected_top_class,
)
from src.extraction_runtime.mcp.test_launch import _generated_server
from src.extraction_runtime.steps.extensions.kg import resolve_enrichment_targets
from src.extraction_runtime.steps.extensions.queue import configured_extensions
from src.kg_building.pipeline.extension import extension_memory_candidate
from src.extraction_runtime.steps.main_extraction.run import _enrichment_tools
from src.extraction_runtime.kg_binding import bind_kg_runtime_context
from src.extraction_runtime.names import entity_artifact_name, generate_hash
from src.extraction_runtime.steps.top_entity.membership import (
    keep_valid_top_entity_lines,
    listing_labels,
)
from src.extraction_runtime.tolerate import extraction_steps_complete
from src.mcp_servers.pubchem.name_dedup import deterministic_name_filter, is_redundant_name
from src.extraction_runtime.paper import (
    load_paper_content_with_sources,
    slim_markdown_path,
)
from src.extraction_runtime.steps.tbox_slim.run import (
    assemble_conversion_source,
    run_step as run_tbox_slim,
    runtime_extraction_model,
    slim_tboxes,
)
from src.extraction_runtime.steps.main_extraction.ledger import (
    salvage_closed_ledger,
    validate_closed_ledger_shape,
)
from src.extraction_runtime.steps.main_extraction.prompts import (
    LOOKUP_ALIAS_RULE_BEGIN,
    append_complete_inheritance_context,
    append_complete_target_passage,
    append_semantic_hint_output_boundary,
    bind_runtime_context,
    inject_lookup_alias_rule,
)
from src.extraction_runtime.steps.registry import STEP_MODULES, load_step_module
from src.extraction_runtime.steps.top_entity.identity import resolve_top_class


class ExtractionRuntimeImportTests(unittest.TestCase):
    def test_cli_parser_requires_ontology(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["ontosynthesis", "--test"])
        self.assertEqual(args.ontology, "ontosynthesis")
        self.assertTrue(args.test)

    def test_step_modules_import(self) -> None:
        for name in STEP_MODULES:
            module = load_step_module(name)
            self.assertIsNotNone(module, name)
            self.assertTrue(hasattr(module, "run_step"), name)

    def test_domain_binding_does_not_read_meta_task(self) -> None:
        chemistry = load_runtime_domain("ontosynthesis")
        medical = load_runtime_domain("medical")
        ontomops = load_runtime_domain("ontomops")
        self.assertEqual(chemistry.default_steps, COMPLEX_MAIN_STEPS + ["mop_derivation"])
        self.assertEqual(medical.default_steps, SIMPLE_MAIN_STEPS)
        self.assertEqual(ontomops.default_steps, EXTENSION_STEPS + ["mop_derivation"])
        self.assertEqual(chemistry.scenario_domain, "mops")
        self.assertEqual(medical.scenario_domain, "medical")
        self.assertFalse(chemistry.vision_required)
        self.assertTrue(medical.vision_required)
        self.assertEqual(medical.execution_profile, "simple_main")
        from src.extraction_runtime.locked_mechanisms import extraction_revision_enabled

        self.assertFalse(
            extraction_revision_enabled({"execution_profile": medical.execution_profile})
        )
        self.assertTrue(
            extraction_revision_enabled(
                {"execution_profile": chemistry.execution_profile}
            )
        )
        self.assertNotIn("section_classification", chemistry.default_steps)
        self.assertNotIn("stitching", chemistry.default_steps)
        self.assertNotIn("stitching", medical.default_steps)
        self.assertNotIn("full_document_stitch", chemistry.runtime)
        self.assertNotIn("full_document_stitch", medical.runtime)
        self.assertNotIn("meta_task", json.dumps(chemistry.runtime))
        jobs = resolve_derivation_jobs({"domain": chemistry})
        self.assertEqual([job["ontology_name"] for job in jobs], ["ontomops"])
        self.assertEqual(jobs[0]["output_dir"], "ontomops_output")
        self.assertTrue(jobs[0]["agents"])

    def test_runtime_reads_mcp_and_extension_bindings_from_domain_config(self) -> None:
        chemistry = load_runtime_domain("ontosynthesis")
        medical = load_runtime_domain("medical")
        ontomops = load_runtime_domain("ontomops")
        self.assertEqual(
            _enrichment_tools({"domain": chemistry}),
            ("chemistry.json", ["pubchem", "enhanced_websearch", "ccdc"]),
        )
        self.assertEqual(_enrichment_tools({"domain": medical}), ("", []))
        self.assertEqual(_enrichment_tools({}), ("", []))
        parent_extensions = configured_extensions({"domain": chemistry})
        self.assertEqual(
            [item["name"] for item in parent_extensions],
            ["ontomops", "ontospecies"],
        )
        for item in parent_extensions:
            self.assertEqual(item["upstream_ontology"], "ontosynthesis")
            self.assertTrue(item.get("enrichment_target"))
            self.assertEqual(item["enrichment_target"]["root_variable"], "synthesis")
            self.assertTrue(str(item.get("bridge_class_iri") or "").startswith("http"))
        child = configured_extensions({"domain": ontomops})
        self.assertEqual(len(child), 1)
        self.assertEqual(child[0]["upstream_ontology"], "ontosynthesis")
        self.assertEqual(child[0]["enrichment_target"]["root_variable"], "synthesis")
        self.assertEqual(child[0]["mcp_set_name"], "extension.json")
        self.assertEqual(
            child[0]["bridge_class_iri"],
            "https://www.theworldavatar.com/kg/ontomops/MetalOrganicPolyhedron",
        )

    def test_enrichment_target_defaults_are_compiler_neutral(self) -> None:
        defaults = resolve_enrichment_targets.__kwdefaults__
        self.assertEqual(defaults["root_variable"], "root")
        self.assertEqual(defaults["target_variable"], "target")
        self.assertEqual(defaults["cardinality"], "exactly_one")

    def test_scenario_templates_have_no_meta_task(self) -> None:
        root = repository_root()
        mops = json.loads(
            (root / "configs" / "scenarios" / "pipeline_mops.json").read_text(
                encoding="utf-8"
            )
        )
        medical = json.loads(
            (root / "configs" / "scenarios" / "pipeline_medical.json").read_text(
                encoding="utf-8"
            )
        )
        for payload in (mops, medical):
            self.assertNotIn("meta_task_config", payload)
            self.assertIn("steps", payload)
            self.assertNotIn("section_classification", payload["steps"])
            self.assertNotIn("stitching", payload["steps"])
            self.assertEqual(payload["steps"][0], "pdf_conversion")
        self.assertIn("tbox_slim", mops["steps"])
        self.assertEqual(mops["steps"][1], "tbox_slim")
        self.assertNotIn("tbox_slim", medical["steps"])
        self.assertEqual(medical["steps"][1], "top_entity_extraction")
        self.assertTrue(medical.get("vision_pdf_conversion"))


class ArtifactAndPaperTests(unittest.TestCase):
    def test_hash_and_safe_name(self) -> None:
        self.assertEqual(len(generate_hash("10.1021/demo")), 8)
        self.assertEqual(entity_artifact_name("MOP-1: α-test"), "MOP-1_alpha-test")
        self.assertEqual(
            listing_labels("ChemicalSynthesis-1 [UMC-1]\nChemicalSynthesis-2 [UMC-2]\n"),
            ["UMC-1", "UMC-2"],
        )

    def test_load_prompt_splices_inc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "EXTRACTION_ITER_2.md"
            inc = root / "EXTRACTION_ITER_2.materializable.inc"
            prompt.write_text("PROMPT BODY\n", encoding="utf-8")
            inc.write_text(
                "----- DETERMINISTIC T-BOX CONTRACT (mechanically spliced; do not edit) -----\nINC\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"TWA_GENERATED_ARTIFACT_ROOT": str(root)}):
                text = load_prompt(str(prompt))
            self.assertIn("PROMPT BODY", text)
            self.assertIn("DETERMINISTIC T-BOX CONTRACT", text)

    def test_paper_fallback_and_si_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "abcd1234"
            folder.mkdir()
            (folder / "abcd1234_text.md").write_text("MAIN TEXT", encoding="utf-8")
            (folder / "abcd1234_si.md").write_text("SI TEXT", encoding="utf-8")
            content, sources = load_paper_content_with_sources("abcd1234", tmp)
            self.assertIn("MAIN TEXT", content)
            self.assertIn("SI TEXT", content)
            self.assertTrue(any(path.endswith("_text.md") for path in sources))
            self.assertTrue(any(path.endswith("_si.md") for path in sources))

    def test_extension_publish_reads_scoped_memory_not_latest_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "abcd1234"
            main_memory = folder / "memory"
            ext_memory = folder / "memory_ontomops"
            main_memory.mkdir(parents=True)
            ext_memory.mkdir(parents=True)
            wrong = main_memory / "UMC-2.ttl"
            right = ext_memory / "UMC-1.ttl"
            wrong.write_text("# wrong entity\n", encoding="utf-8")
            right.write_text("# scoped entity\n", encoding="utf-8")
            produced = extension_memory_candidate(
                folder, "ontomops", "UMC-1", "https://example.test/UMC-1"
            )
            self.assertEqual(produced, right)
            self.assertIsNone(
                extension_memory_candidate(
                    folder, "ontomops", "UMC-2", "https://example.test/UMC-2"
                )
            )

    def test_pipeline_main_ontology_name_follows_upstream_binding(self) -> None:
        chemistry = load_runtime_domain("ontosynthesis")
        ontomops = load_runtime_domain("ontomops")
        medical = load_runtime_domain("medical")
        self.assertEqual(pipeline_main_ontology_name(chemistry), "ontosynthesis")
        self.assertEqual(chemistry.pipeline_main_ontology_name, "ontosynthesis")
        self.assertEqual(pipeline_main_ontology_name(ontomops), "ontosynthesis")
        self.assertEqual(ontomops.pipeline_main_ontology_name, "ontosynthesis")
        self.assertEqual(pipeline_main_ontology_name(medical), "medical")

    def test_generated_extension_mcp_env_keeps_pipeline_main_ontology(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts" / "ontomops"
            scripts.mkdir(parents=True)
            (scripts / "main.py").write_text("# dummy\n", encoding="utf-8")
            with (
                patch(
                    "src.extraction_runtime.mcp.test_launch.generated_artifact_root",
                    return_value=root,
                ),
                patch(
                    "src.extraction_runtime.mcp.test_launch.scripts_dir",
                    return_value=scripts,
                ),
                patch(
                    "src.extraction_runtime.mcp.test_launch.repository_root",
                    return_value=root,
                ),
            ):
                spec = _generated_server(
                    "ontomops",
                    data_dir=root,
                    main_ontology_name="ontosynthesis",
                )
        self.assertEqual(spec["env"]["TWA_MAIN_ONTOLOGY_NAME"], "ontosynthesis")

    def test_mcp_merge_preserves_pipeline_main_ontology_env(self) -> None:
        merged = merge_mcp_server_environment(
            {"TWA_MAIN_ONTOLOGY_NAME": "ontomops"},
            {"TWA_MAIN_ONTOLOGY_NAME": "ontosynthesis"},
        )
        self.assertEqual(merged["TWA_MAIN_ONTOLOGY_NAME"], "ontosynthesis")

    def test_paper_prefers_slim_without_reappending_si(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "abcd1234"
            folder.mkdir()
            (folder / "abcd1234.md").write_text("FULL MAIN", encoding="utf-8")
            (folder / "abcd1234_si.md").write_text("FULL SI", encoding="utf-8")
            (folder / "abcd1234_slim.md").write_text("SLIM BODY", encoding="utf-8")
            content, sources = load_paper_content_with_sources("abcd1234", tmp)
            self.assertEqual(content, "SLIM BODY")
            self.assertEqual(sources, [str(slim_markdown_path("abcd1234", tmp))])
            self.assertNotIn("FULL SI", content)

    def test_paper_prefers_vision_over_chemistry_slim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "abcd1234"
            folder.mkdir()
            (folder / "abcd1234_vision.md").write_text("VISION BODY", encoding="utf-8")
            (folder / "abcd1234_slim.md").write_text("SLIM BODY", encoding="utf-8")
            content, sources = load_paper_content_with_sources("abcd1234", tmp)
            self.assertEqual(content, "VISION BODY")
            self.assertEqual(sources, [str(folder / "abcd1234_vision.md")])
            self.assertNotIn("SLIM BODY", content)

    def test_tbox_slim_assembles_conversion_md_and_uses_extraction_model(self) -> None:
        chemistry = load_runtime_domain("ontosynthesis")
        medical = load_runtime_domain("medical")
        names = [name for name, _path in slim_tboxes(chemistry)]
        self.assertEqual(names, ["ontosynthesis", "ontomops-subgraph", "ontospecies-subgraph"])
        self.assertEqual([name for name, _path in slim_tboxes(medical)], ["medical_case_schema_de_non_flat_v4"])
        self.assertEqual(
            runtime_extraction_model({"domain": chemistry}),
            "gpt-4.1-2025-04-14",
        )
        self.assertEqual(runtime_extraction_model({"domain": medical}), "gpt-5-2025-08-07")
        self.assertEqual(
            runtime_extraction_model(
                {"domain": medical, "extraction_model": "moonshotai/kimi-k3"}
            ),
            "moonshotai/kimi-k3",
        )
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "abcd1234"
            folder.mkdir()
            (folder / "abcd1234.md").write_text("MAIN PARA\n\n| table |\n", encoding="utf-8")
            (folder / "abcd1234_tables.md").write_text("| table |\n", encoding="utf-8")
            (folder / "abcd1234_si.md").write_text("SI PARA", encoding="utf-8")
            source, parts = assemble_conversion_source("abcd1234", folder)
            used = {item["label"]: item["used"] for item in parts}
            self.assertTrue(used["main manuscript"])
            self.assertTrue(used["supporting information"])
            self.assertFalse(used["main tables"])
            self.assertIn("MAIN PARA", source)
            self.assertIn("SI PARA", source)
            self.assertEqual(source.count("| table |"), 1)

    def test_tbox_slim_uses_0827_si_md_not_si_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "abcd1234"
            folder.mkdir()
            (folder / "abcd1234.md").write_text("MAIN", encoding="utf-8")
            (folder / "abcd1234_si.md").write_text("SI MD", encoding="utf-8")
            (folder / "abcd1234_si_text.md").write_text("SI TEXT", encoding="utf-8")
            source, parts = assemble_conversion_source("abcd1234", folder)
            used = [item for item in parts if item["label"] == "supporting information"][0]
            self.assertTrue(used["used"])
            self.assertTrue(used["path"].endswith("abcd1234_si.md"))
            self.assertIn("SI MD", source)
            self.assertNotIn("SI TEXT", source)

    def test_tbox_slim_step_writes_and_skips(self) -> None:
        chemistry = load_runtime_domain("ontosynthesis")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "abcd1234"
            folder.mkdir()
            (folder / "abcd1234.md").write_text("Keep this procedure.", encoding="utf-8")
            with patch(
                "src.extraction_runtime.steps.tbox_slim.run.invoke_text",
                return_value="```markdown\nKeep this procedure.\n```",
            ) as mocked:
                ok = run_tbox_slim("abcd1234", {"data_dir": tmp, "domain": chemistry})
            self.assertTrue(ok)
            mocked.assert_called_once()
            self.assertEqual(mocked.call_args.kwargs["model_name"], "gpt-4.1-2025-04-14")
            slim = folder / "abcd1234_slim.md"
            self.assertEqual(slim.read_text(encoding="utf-8"), "Keep this procedure.\n")
            self.assertTrue((folder / "abcd1234_slim.meta.json").is_file())
            with patch(
                "src.extraction_runtime.steps.tbox_slim.run.invoke_text",
                side_effect=AssertionError("should skip"),
            ):
                self.assertTrue(
                    run_tbox_slim("abcd1234", {"data_dir": tmp, "domain": chemistry})
                )

    def test_bind_runtime_context_injects_missing_slots(self) -> None:
        bound = bind_runtime_context(
            "Extract the procedure.",
            doi_hash="abcd1234",
            entity_label="MOP-1",
            entity_uri="http://example.org/mop-1",
            source_text="paper body",
            accumulated_hints="prior hints",
            identity_dossier={"label": "MOP-1"},
        )
        self.assertIn("MOP-1", bound)
        self.assertIn("http://example.org/mop-1", bound)
        self.assertIn("paper body", bound)
        self.assertIn("prior hints", bound)
        self.assertIn("authoritative identity scope", bound)
        self.assertIn(
            "Do not substitute, merge, or redirect the current entity to another top-entity scope.",
            bound,
        )
        self.assertIn("read-only semantic identity registry", bound)
        self.assertIn("PIPELINE-INJECTED LOOKUP ALIAS RULE", bound)
        self.assertIn("do not paste the payload", bound)
        self.assertEqual(bound.count(LOOKUP_ALIAS_RULE_BEGIN), 1)
        self.assertEqual(inject_lookup_alias_rule(bound).count(LOOKUP_ALIAS_RULE_BEGIN), 1)

    def test_extraction_revision_budgets_are_locked(self) -> None:
        from src.extraction_runtime.locked_mechanisms import (
            EXTRACTION_REVISION_LOCKED,
            MAIN_EXTRACTION_REVISION_ATTEMPTS,
            PRE_CLOSED_LEDGER_REVISION_ATTEMPTS,
            TOP_ENTITY_REVISION_ATTEMPTS,
            is_closed_ledger_source,
            is_semantic_hints,
        )

        self.assertTrue(EXTRACTION_REVISION_LOCKED)
        self.assertEqual(MAIN_EXTRACTION_REVISION_ATTEMPTS, 5)
        self.assertEqual(PRE_CLOSED_LEDGER_REVISION_ATTEMPTS, 8)
        self.assertEqual(TOP_ENTITY_REVISION_ATTEMPTS, 5)
        self.assertTrue(is_semantic_hints("SEMANTIC_HINTS_V1\nAdd H2TBI"))
        self.assertFalse(is_semantic_hints("Add H2TBI"))
        self.assertFalse(is_closed_ledger_source("full paper text"))

    def test_complete_target_passage_and_semantic_header_contract(self) -> None:
        ledger = json.dumps(
            {
                "scope_resolution": {
                    "target_evidence": [
                        "Add H2TBI then heat the mixture and crystallize Cr-2."
                    ]
                },
                "evidence": [],
            }
        )
        prompt = append_complete_target_passage("Extract the workflow.", ledger)
        self.assertIn("PIPELINE-INJECTED COMPLETE TARGET PASSAGE", prompt)
        self.assertIn("identity anchor, not a start bound", prompt)
        self.assertIn("crystallize Cr-2", prompt)
        brief = (
            '---- BRIEF ----\n{"effective_workflow": [], "dependencies": [], '
            '"base_workflows": [{"atoms": [{"source_evidence": "same as Cr-1"}]}]}'
        )
        inherited = append_complete_inheritance_context("Extract the workflow.", brief)
        self.assertIn("PIPELINE-INJECTED COMPLETE INHERITANCE CONTEXT", inherited)
        self.assertIn("same as Cr-1", inherited)
        semantic = append_semantic_hint_output_boundary("Extract the workflow.")
        self.assertIn("`SEMANTIC_HINTS_V1`", semantic)

    def test_salvage_closed_ledger_drops_untyped_rows(self) -> None:
        raw = """
        {
          "scope_resolution": {
            "completion_attestation": {
              "target_located": true,
              "all_references_resolved": true,
              "all_modifications_applied": true,
              "effective_workflow_complete": true
            }
          },
          "evidence": [
            {
              "evidence_id": "E001",
              "source_order": 1,
              "ordering_cue": "",
              "verbatim_quote": "H2TBI (200 mg) dissolved in 5 mL DMA",
              "candidate_types": ["Add"],
              "candidate_properties": {"hasAmount": "200 mg"}
            },
            {
              "evidence_id": "E005",
              "source_order": 5,
              "ordering_cue": "",
              "verbatim_quote": "were mixed and placed in a 20 mL vial",
              "candidate_types": [],
              "candidate_properties": {}
            },
            {
              "evidence_id": "E007",
              "source_order": 7,
              "ordering_cue": "",
              "verbatim_quote": "Purple block crystals of Cr-2 were obtained.",
              "candidate_types": [],
              "candidate_properties": {}
            }
          ]
        }
        """
        self.assertTrue(validate_closed_ledger_shape(raw))
        salvaged = salvage_closed_ledger(raw)
        self.assertIsNotNone(salvaged)
        self.assertFalse(validate_closed_ledger_shape(salvaged or ""))
        payload = json.loads(salvaged or "{}")
        self.assertEqual(len(payload["evidence"]), 1)
        self.assertEqual(payload["evidence"][0]["candidate_types"], ["Add"])

    def test_keep_valid_top_entity_lines_drops_bad_rows(self) -> None:
        text = "ChemicalSynthesis-1 [Cr-1]\nNotAPrefix-2 [junk]\nChemicalSynthesis-2 [Cr-2]\n"
        kept = keep_valid_top_entity_lines(text, ["ChemicalSynthesis"])
        self.assertIn("ChemicalSynthesis-1 [Cr-1]", kept)
        self.assertIn("ChemicalSynthesis-2 [Cr-2]", kept)
        self.assertNotIn("NotAPrefix", kept)

    def test_extraction_steps_complete_checks_extract_markers_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            steps = [
                "top_entity_extraction",
                "main_ontology_extractions",
                "main_kg_building",
                "extensions_extractions",
            ]
            self.assertFalse(extraction_steps_complete(folder, steps))
            (folder / "top_entities.txt").write_text("ChemicalSynthesis-1 [A]\n")
            (folder / ".main_ontology_extractions_done").write_text("completed\n")
            self.assertFalse(extraction_steps_complete(folder, steps))
            (folder / ".extensions_extractions_done").write_text("completed\n")
            self.assertTrue(extraction_steps_complete(folder, steps))
            self.assertTrue(
                extraction_steps_complete(folder, ["main_kg_building", "mop_derivation"])
            )

    def test_process_doi_retries_once_when_extract_markers_missing(self) -> None:
        from src.extraction_runtime.runner import process_doi

        class _Step:
            def __init__(self) -> None:
                self.calls = 0

            def run_step(self, doi_hash: str, config: dict) -> bool:
                self.calls += 1
                folder = Path(config["data_dir"]) / doi_hash
                folder.mkdir(parents=True, exist_ok=True)
                if self.calls >= 2:
                    (folder / ".main_ontology_extractions_done").write_text(
                        "completed\n", encoding="utf-8"
                    )
                return True

        step = _Step()
        domain = load_runtime_domain("ontosynthesis")
        with tempfile.TemporaryDirectory() as tmp, patch(
            "src.extraction_runtime.runner.load_step_module", return_value=step
        ):
            process_doi(
                doi_hash="abcd1234",
                steps=["main_ontology_extractions"],
                config={},
                data_dir=tmp,
                domain=domain,
            )
            self.assertEqual(step.calls, 2)
            self.assertTrue(
                (Path(tmp) / "abcd1234" / ".main_ontology_extractions_done").is_file()
            )

    def test_pubchem_filters_catalog_and_registry_junk(self) -> None:
        self.assertTrue(is_redundant_name("Eye Wash Station"))
        self.assertTrue(is_redundant_name("DTXSID7020182"))
        self.assertTrue(is_redundant_name("64-17-5"))
        self.assertFalse(is_redundant_name("ethanol"))
        self.assertEqual(
            deterministic_name_filter(
                ["ethanol", "Eye Wash", "64-17-5", "Ethanol, 99.8%"]
            ),
            ["ethanol"],
        )

    def test_kg_binding_is_official_no_contract(self) -> None:
        text = bind_kg_runtime_context(
            hints="SEMANTIC_HINTS_V1\nstep-1",
            doi="10.1021/acsami.7b18836",
            entity_label="UMC-1",
            entity_uri="http://example.org/umc-1",
        )
        self.assertIn("SEMANTIC_HINTS_V1", text)
        self.assertIn("ExtractedHints:", text)
        self.assertIn("Do not downgrade an explicit canonical field", text)
        self.assertIn("Graph lifecycle instructions:", text)
        self.assertIn("10.1021/acsami.7b18836", text)
        self.assertIn("Bound root label: UMC-1", text)
        self.assertIn("Bound root IRI: http://example.org/umc-1", text)
        self.assertNotIn("PIPELINE-INJECTED SOURCE TEXT", text)
        self.assertNotIn("paper text as evidence", text)
        self.assertNotIn("KG_BINDING", text)
        self.assertNotIn("KG_BUILDING_ITER_", text)
        self.assertNotIn("Experimental Section", text)
        self.assertNotIn("One Add owns exactly one ontosyn:hasAddedChemicalInput", text)
        self.assertNotIn("# Generic-noprompt graph rules", text)

    def test_top_class_from_generated_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_dir = root / "ontology_structures" / "ontosynthesis"
            contract_dir.mkdir(parents=True)
            (contract_dir / "generation_contract.json").write_text(
                json.dumps(
                    {
                        "top_entity": {
                            "class_local": "PlannedClass",
                            "class_iri": "http://example.org/PlannedClass",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"TWA_GENERATED_ARTIFACT_ROOT": str(root)}):
                top = selected_top_class("ontosynthesis")
                resolved = resolve_top_class({"ontology_name": "ontosynthesis"})
            self.assertEqual(top["class_local"], "PlannedClass")
            self.assertEqual(resolved["class_iri"], "http://example.org/PlannedClass")

    def test_discover_dois_from_pdf_stems(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_dir = Path(tmp) / "pdfs"
            runtime = Path(tmp) / "runtime"
            input_dir.mkdir()
            runtime.mkdir()
            (input_dir / "10.1021_demo.pdf").write_bytes(b"%PDF-1.4")
            (input_dir / "10.1021_demo_si.pdf").write_bytes(b"%PDF-1.4")
            mapping = discover_dois(input_dir, runtime)
            self.assertEqual(len(mapping), 1)
            doi_hash = next(iter(mapping.values()))
            self.assertEqual(len(doi_hash), 8)
            self.assertTrue((runtime / "doi_to_hash.json").is_file())

    def test_test_launch_reads_mcp_capabilities(self) -> None:
        from src.extraction_runtime.mcp.test_launch import _capability_tools

        tools = _capability_tools(
            {
                "kg_building": {"tools": ["llm_created_mcp"]},
                "extensions": {"tools": ["mops_extension", "ccdc"]},
            },
            "kg_building",
            "extensions",
        )
        self.assertEqual(tools, ["llm_created_mcp", "mops_extension", "ccdc"])

    def test_generated_root_env_override_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "prompts").mkdir()
            with patch.dict(os.environ, {"TWA_GENERATED_ARTIFACT_ROOT": str(root)}):
                self.assertEqual(generated_artifact_root(), root.resolve())


if __name__ == "__main__":
    unittest.main()
