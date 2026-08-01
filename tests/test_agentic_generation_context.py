from __future__ import annotations

import unittest
from pathlib import Path

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)


class TestAgenticGenerationContext(unittest.TestCase):
    def test_builds_medical_and_ontosynthesis_contexts_from_configs(self) -> None:
        cases = {
            "medical": "medical_case/medical_case_schema_de_non_flat_v3.ttl",
            "ontosynthesis": "data/ontologies/ontosynthesis.ttl",
        }
        for ontology, ttl in cases.items():
            with self.subTest(ontology=ontology):
                ctx = build_agentic_generation_context(
                    ontology_name=ontology,
                    output_root=Path("tmp/agentic_generation/test_context"),
                    write_files=False,
                )
                self.assertEqual(ctx.ontology.name, ontology)
                self.assertEqual(ctx.ontology.ttl_file, ttl)
                self.assertTrue(ctx.parsed.get("classes"))
                self.assertTrue(ctx.parsed.get("properties"))
                self.assertEqual(ctx.contract.get("ontology_name"), ontology)
                self.assertIn("top_entity", ctx.contract)
                self.assertIn("ordered_member_profile", ctx.contract)
                self.assertNotIn("runtime_policies", ctx.contract)
                self.assertEqual(
                    ctx.config_provenance["boundary"]["semantic_authority"],
                    "tbox",
                )

    def test_write_files_places_context_under_isolated_output_root(self) -> None:
        root = Path("tmp/agentic_generation/test_context_write")
        ctx = build_agentic_generation_context(
            ontology_name="medical",
            output_root=root,
            write_files=True,
        )
        self.assertTrue(Path(ctx.parsed_summary_path).is_file())
        self.assertTrue(Path(ctx.parsed_markdown_path).is_file())
        self.assertTrue(Path(ctx.contract_path).is_file())
        self.assertTrue(Path(ctx.integrity_profile_path).is_file())
        self.assertTrue(Path(ctx.config_provenance_path).is_file())
        self.assertTrue(Path(ctx.parsed_summary_path).as_posix().startswith(root.as_posix()))

    def test_ontosynthesis_iteration_plan_is_compiled_against_active_tbox(self) -> None:
        ctx = build_agentic_generation_context(
            ontology_name="ontosynthesis",
            output_root=Path("tmp/agentic_generation/test_compiled_iterations"),
            write_files=False,
        )

        self.assertEqual(
            ctx.iteration_blueprint["schema_version"],
            "compiled-iteration-plan.v1",
        )
        iteration_2 = next(
            item
            for item in ctx.iteration_blueprint["iterations"]
            if item["iteration_number"] == 2
        )
        semantic_scope = iteration_2["semantic_scope"]
        self.assertEqual(semantic_scope["source"], "active_tbox")
        self.assertIn(
            "http://purl.org/ontology/bibo/Document",
            {item["iri"] for item in semantic_scope["classes"]},
        )
        self.assertIn(
            "https://www.theworldavatar.com/kg/OntoSyn/retrievedFrom",
            {item["iri"] for item in semantic_scope["object_properties"]},
        )
        self.assertEqual(
            ctx.iteration_blueprint["provenance"]["scheduling_intent"]["source"],
            "non_tbox_scheduling_intent",
        )


if __name__ == "__main__":
    unittest.main()
