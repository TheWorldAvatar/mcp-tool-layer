"""Offline contract tests for chemistry MCP-enabled extraction."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_prompt_slice,
)
from src.agents.scripts_and_prompts_generation.ttl_parser import parse_ontology_ttl


ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "configs/meta_task/meta_task_config.json"
TBOX = ROOT / "data/ontologies/ontosynthesis.ttl"
BLUEPRINT = ROOT / "configs/meta_task/ontosynthesis_iterations_blueprint.json"


class TestChemistryMcpIntegration(unittest.TestCase):
    def test_union_domain_properties_are_attached_to_chemical_input(self) -> None:
        parsed = parse_ontology_ttl(str(TBOX))
        fields = (parsed["classes"]["ChemicalInput"].get("datatype_properties") or {})
        self.assertIn("hasAlternativeNames", fields)
        self.assertIn("hasChemicalFormula", fields)
        self.assertIn("hasChemicalDescription", fields)

    def test_iter2_requires_pubchem_activity(self) -> None:
        blueprint = json.loads(BLUEPRINT.read_text(encoding="utf-8"))
        iteration = next(
            item for item in blueprint["iterations"] if item["iteration_number"] == 2
        )
        groups = (iteration.get("extraction_validation") or {}).get(
            "required_executed_tool_groups"
        ) or []
        self.assertTrue(groups)
        self.assertIn("search_pubchem_by_name", groups[0]["any_of"])

    def test_generated_iter2_prompt_requires_chemistry_enrichment(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="chemistry_prompt_") as tmp:
            context = build_agentic_generation_context(
                ontology_name="ontosynthesis",
                meta_task_config_path=META,
                output_root=Path(tmp),
                write_files=True,
            )
            generate_deterministic_prompt_slice(context)
            prompt = (
                Path(context.prompts_dir) / "EXTRACTION_ITER_2.md"
            ).read_text(encoding="utf-8")
            self.assertIn("External Chemistry Enrichment", prompt)
            self.assertIn("call a PubChem lookup tool", prompt)
            self.assertIn("hasAlternativeNames", prompt)
            self.assertIn("hasChemicalFormula", prompt)


if __name__ == "__main__":
    unittest.main()
