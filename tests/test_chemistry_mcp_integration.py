"""Offline contract tests for chemistry MCP-enabled extraction."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from models.MCPConfig import (
    load_mcp_set_extraction_validation,
    load_mcp_set_tool_purposes,
)
from src.pipelines.main_ontology_extractions.extract import (
    _format_required_tool_feedback,
    _should_fail_open_required_tool_gate,
    _inject_required_tool_contract,
    _merge_mcp_set_extraction_validation,
    _required_executed_tool_groups,
)
from src.agents.scripts_and_prompts_generation.ttl_parser import parse_ontology_ttl


ROOT = Path(__file__).resolve().parents[1]
TBOX = ROOT / "data/ontologies/ontosynthesis.ttl"
BLUEPRINT = ROOT / "configs/meta_task/ontosynthesis_iterations_blueprint.json"


class TestChemistryMcpIntegration(unittest.TestCase):
    def test_union_domain_properties_are_attached_to_chemical_input(self) -> None:
        parsed = parse_ontology_ttl(str(TBOX))
        fields = (parsed["classes"]["ChemicalInput"].get("datatype_properties") or {})
        self.assertIn("hasAlternativeNames", fields)
        self.assertIn("hasChemicalFormula", fields)
        self.assertIn("hasChemicalDescription", fields)

    def test_chemistry_mcp_set_owns_pubchem_tool_gate(self) -> None:
        validation = load_mcp_set_extraction_validation("chemistry.json")
        groups = validation.get("required_executed_tool_groups") or []
        self.assertTrue(groups)
        self.assertEqual(groups[0]["name"], "chemical_identity_lookup")
        self.assertIn("search_pubchem_by_name", groups[0]["any_of"])
        merged = _merge_mcp_set_extraction_validation({}, "chemistry.json")
        self.assertEqual(
            merged["required_executed_tool_groups"][0]["name"],
            "chemical_identity_lookup",
        )
        purposes = load_mcp_set_tool_purposes("chemistry.json")
        self.assertIn("pubchem", purposes)
        self.assertIn("ccdc", purposes)
        self.assertIn("enhanced_websearch", purposes)
        self.assertIn("PubChem identifiers", purposes["pubchem"])
        self.assertIn("CCDC identifiers", purposes["ccdc"])

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

    def test_live_compiled_iter2_keeps_pubchem_tool_gate(self) -> None:
        live = json.loads(
            (
                ROOT
                / "ai_generated_contents_ontosyn_regen_v3/iterations/ontosynthesis/iterations.json"
            ).read_text(encoding="utf-8")
        )
        iteration = next(
            item for item in live["iterations"] if item["iteration_number"] == 2
        )
        groups = (iteration.get("extraction_validation") or {}).get(
            "required_executed_tool_groups"
        ) or []
        self.assertTrue(groups)
        self.assertIn("search_pubchem_by_name", groups[0]["any_of"])

    def test_live_compiled_iter3_uses_pubchem_tool_gate(self) -> None:
        live = json.loads(
            (
                ROOT
                / "ai_generated_contents_ontosyn_regen_v3/iterations/ontosynthesis/iterations.json"
            ).read_text(encoding="utf-8")
        )
        iteration = next(
            item for item in live["iterations"] if item["iteration_number"] == 3
        )
        self.assertTrue(iteration.get("use_agent"))
        self.assertIn("pubchem", iteration.get("extraction_mcp_tools") or [])
        groups = (iteration.get("extraction_validation") or {}).get(
            "required_executed_tool_groups"
        ) or []
        self.assertTrue(groups)
        self.assertIn("search_pubchem_by_name", groups[0]["any_of"])

    def test_runtime_injects_required_tool_contract_without_regeneration(self) -> None:
        blueprint = json.loads(BLUEPRINT.read_text(encoding="utf-8"))
        iteration = next(
            item for item in blueprint["iterations"] if item["iteration_number"] == 2
        )
        validation = iteration.get("extraction_validation") or {}
        base_prompt = (
            "Extract facts.\n\n"
            "Return only the single required JSON object."
        )
        injected = _inject_required_tool_contract(
            base_prompt,
            validation,
            use_agent=True,
        )
        self.assertIn("## Required MCP Tool Contract", injected)
        self.assertIn("search_pubchem_by_name", injected)
        self.assertIn("call at least one of", injected)
        self.assertIn("for every applicable entity occurrence", injected)
        self.assertIn("arguments identifying that entity", injected)
        self.assertIn("does not satisfy the requirement for another entity", injected)
        self.assertIn("unresolved", injected)
        self.assertIn("do not invent", injected.lower())
        # Idempotent: do not duplicate the contract block.
        again = _inject_required_tool_contract(
            injected,
            validation,
            use_agent=True,
        )
        self.assertEqual(injected.count("## Required MCP Tool Contract"), 1)
        self.assertEqual(again, injected)
        # Non-agent mode must not inject.
        plain = _inject_required_tool_contract(
            base_prompt,
            validation,
            use_agent=False,
        )
        self.assertEqual(plain, base_prompt)

    def test_tool_gate_feedback_asks_for_retry_with_tool_calls(self) -> None:
        groups = _required_executed_tool_groups(
            {
                "required_executed_tool_groups": [
                    {
                        "name": "chemical_identity_lookup",
                        "any_of": [
                            "search_pubchem_by_name",
                            "search_pubchem_by_smiles",
                        ],
                    }
                ]
            }
        )
        feedback = _format_required_tool_feedback(
            [
                "chemical_identity_lookup requires one of "
                "['search_pubchem_by_name', 'search_pubchem_by_smiles']; executed=[]"
            ],
            groups=groups,
            executed_tool_names=[],
        )
        self.assertIn("REQUIRED MCP TOOL ACTIVITY FEEDBACK:", feedback)
        self.assertIn("ACTION FOR THIS RETRY:", feedback)
        self.assertIn("every applicable entity occurrence not yet covered", feedback)
        self.assertIn("arguments identifying that entity", feedback)
        self.assertIn("call at least one required tool", feedback)
        self.assertIn("Do not answer with JSON-only output", feedback)
        self.assertNotIn("Return corrected extraction hints only", feedback)

    def test_required_tool_gate_fail_opens_on_last_nonempty_draft(self) -> None:
        self.assertFalse(
            _should_fail_open_required_tool_gate(
                attempt=0, max_retries=5, content='{"entities":[]}'
            )
        )
        self.assertFalse(
            _should_fail_open_required_tool_gate(
                attempt=4, max_retries=5, content=""
            )
        )
        self.assertTrue(
            _should_fail_open_required_tool_gate(
                attempt=4, max_retries=5, content='{"entities":[]}'
            )
        )


if __name__ == "__main__":
    unittest.main()
