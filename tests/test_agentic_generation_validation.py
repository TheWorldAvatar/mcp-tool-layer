from __future__ import annotations

import unittest
from pathlib import Path

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    build_agentic_generation_context,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
    generate_deterministic_prompt_slice,
    generate_deterministic_script_slice,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    MEDICAL_CSV_ROUNDTRIP_PROMPT_HEADER,
    build_validation_report,
)


class TestAgenticGenerationValidation(unittest.TestCase):
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

    def test_medical_prompt_missing_csv_contract_is_rejected(self) -> None:
        root = Path("tmp/agentic_generation/test_medical_csv_roundtrip_negative")
        ctx = build_agentic_generation_context(
            ontology_name="medical",
            output_root=root,
            write_files=True,
        )
        generate_deterministic_prompt_slice(ctx)
        path = Path(ctx.prompts_dir) / "EXTRACTION_ITER_2.md"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(MEDICAL_CSV_ROUNDTRIP_PROMPT_HEADER, "## REMOVED HEADER TEST"),
            encoding="utf-8",
        )
        report = build_validation_report(ctx, foreign_contracts=[], write_report=False)
        self.assertFalse(report["ok"])
        self.assertTrue(any("CSV round-trip" in msg for msg in report["failures"]))

    def test_medical_prompt_missing_mutual_exclusion_contract_is_rejected(self) -> None:
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
        self.assertFalse(report["ok"])
        self.assertTrue(
            any("mutually exclusive property contract" in msg for msg in report["failures"])
        )

    def test_validation_reports_cross_ontology_prompt_residue_from_contracts(self) -> None:
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
        generate_deterministic_script_slice(medical)
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

        report = build_validation_report(
            medical,
            foreign_contracts=[ontosynthesis.contract],
            write_report=False,
        )
        self.assertFalse(report["ok"])
        self.assertTrue(any("Foreign ontology symbols" in msg for msg in report["failures"]))


if __name__ == "__main__":
    unittest.main()
