"""Slim-family extraction prompt lock: accept 09-05 style, reject e2e1."""

from __future__ import annotations

import unittest

from src.extraction_prompt_generation.slim_family_lock import (
    assert_slim_extraction_prompt_family,
    slim_family_failures,
)
from src.extraction_runtime.slim_extract_defaults import (
    LOCKED_EXTRACT_STEPS,
    SLIM_EXTRACT_STEPS,
    apply_slim_ontosynthesis_extract_defaults,
)


SLIM_ITER1 = """Purpose
- Identify top entities.

Authoritative T-Box scope (verbatim)
[Definition]
Represents one standalone synthetic procedure.

Identity-bearing label
- The top-entity label must be the most specific source-supported procedure identity.

Output format
- Return exactly one JSON object with the literal top-level fields entities and relations.
"""

E2E1_ITER1 = """# Extraction Prompt: ontosynthesis

Output:
Return only normalized top-entity lines, with no JSON, markdown fences, bullets, or explanatory text.
The label inside brackets should be the shortest stable source-supported identifier for the selected top entity.
"""

SLIM_ITER3 = """RUNTIME INPUTS (bound once; refer back without repeating placeholders)
- Source paper content: {paper_content}

OUTPUT POLICY
- Begin your answer with the exact header: SEMANTIC_HINTS_V1
- emit each as a standalone property-local line (e.g., hasOrder: 3)
"""

E2E1_ITER3 = """# Extraction Prompt: ontosynthesis Iteration 3

Rules:
- Return only a natural-language ledger headed exactly `SEMANTIC_HINTS_V1`.
- Do not require the heading form `<SubclassLocal> (Order: <n>)`.
"""


class SlimFamilyLockTests(unittest.TestCase):
    def test_accepts_slim_iter1_and_iter3(self) -> None:
        self.assertEqual(slim_family_failures("EXTRACTION_ITER_1.md", SLIM_ITER1), [])
        self.assertEqual(slim_family_failures("EXTRACTION_ITER_3.md", SLIM_ITER3), [])
        assert_slim_extraction_prompt_family("EXTRACTION_ITER_1.md", SLIM_ITER1)

    def test_rejects_e2e1_iter1(self) -> None:
        failures = slim_family_failures("EXTRACTION_ITER_1.md", E2E1_ITER1)
        self.assertTrue(failures)
        with self.assertRaises(ValueError):
            assert_slim_extraction_prompt_family("EXTRACTION_ITER_1.md", E2E1_ITER1)

    def test_rejects_e2e1_iter3(self) -> None:
        failures = slim_family_failures("EXTRACTION_ITER_3.md", E2E1_ITER3)
        self.assertTrue(any("e2e1/promptfix" in item for item in failures))

    def test_accepts_semantic_text_named_iter1(self) -> None:
        self.assertEqual(
            slim_family_failures("EXTRACTION_ITER_1.md", SLIM_ITER3),
            [],
        )
        assert_slim_extraction_prompt_family("EXTRACTION_ITER_1.md", SLIM_ITER3)
        self.assertEqual(
            slim_family_failures("EXTRACTION_ITER_2.md", "PROMPT BODY\n"),
            [],
        )

    def test_mint_defaults_lock_ontosynthesis_extract(self) -> None:
        config = {"ontology": "ontosynthesis", "steps": []}
        apply_slim_ontosynthesis_extract_defaults(config, steps_were_explicit=False)
        self.assertEqual(config["steps"], list(LOCKED_EXTRACT_STEPS))
        self.assertEqual(config["steps"], list(SLIM_EXTRACT_STEPS))
        self.assertNotIn("reuse_conversion_artifacts_from", config)
        self.assertFalse(config["reuse_stitched_markdown"])
        self.assertTrue(config["compare_one_shot"])

    def test_extract_only_steps_gain_pdf_and_slim_prefix(self) -> None:
        config = {
            "ontology": "ontosynthesis",
            "reuse_conversion_artifacts_from": "scenarios/mops/datasets/gpt41_tbox_slim",
            "steps": [
                "top_entity_extraction",
                "top_entity_kg_building",
                "main_ontology_extractions",
            ],
        }
        apply_slim_ontosynthesis_extract_defaults(config, steps_were_explicit=True)
        self.assertEqual(config["steps"][:2], ["pdf_conversion", "tbox_slim"])
        self.assertNotIn("reuse_conversion_artifacts_from", config)
        self.assertFalse(config["reuse_stitched_markdown"])

    def test_kg_only_and_protocol_are_untouched(self) -> None:
        kg = {"ontology": "ontosynthesis", "steps": ["main_kg_building"]}
        apply_slim_ontosynthesis_extract_defaults(kg, steps_were_explicit=True)
        self.assertNotIn("compare_one_shot", kg)
        proto = {
            "ontology": "ontosynthesis",
            "experiment_protocol": "generic-strict",
            "steps": ["main_kg_building"],
        }
        apply_slim_ontosynthesis_extract_defaults(proto, steps_were_explicit=True)
        self.assertNotIn("reuse_conversion_artifacts_from", proto)


if __name__ == "__main__":
    unittest.main()
