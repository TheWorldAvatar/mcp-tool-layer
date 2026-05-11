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
        self.assertTrue(Path(ctx.parsed_summary_path).as_posix().startswith(root.as_posix()))


if __name__ == "__main__":
    unittest.main()
