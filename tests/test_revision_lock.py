"""Extraction revision is locked on; KG revision is locked off."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.extraction_runtime.locked_mechanisms import (
    EXTRACTION_REVISION_LOCKED,
    MAIN_EXTRACTION_REVISION_ATTEMPTS,
    ONE_SHOT_ENV,
    PRE_CLOSED_LEDGER_REVISION_ATTEMPTS,
    TOP_ENTITY_REVISION_ATTEMPTS,
    apply_extraction_revision_lock,
    extraction_revision_complete,
    extraction_revision_enabled,
    one_shot_extraction_enabled,
    revision_feedback_fingerprint,
    same_revision_feedback,
    skip_extraction_judges,
    skip_if_already_revised,
    write_extraction_revision_receipt,
)
from src.kg_building.experiment_protocol import (
    apply_to_pipeline_config,
    lock_revision_policy,
    ox_summary_fields,
    resolve_protocol,
)
from src.kg_building.revision_lock import (
    KG_HINT_REVISION_MAX_ATTEMPTS,
    KG_MAX_ATTEMPTS,
    KG_REVISION_LOCKED_OFF,
    apply_kg_revision_lock,
)


class ExtractionRevisionLockTests(unittest.TestCase):
    def test_extraction_revision_cannot_be_disabled(self) -> None:
        self.assertTrue(EXTRACTION_REVISION_LOCKED)
        self.assertEqual(MAIN_EXTRACTION_REVISION_ATTEMPTS, 5)
        self.assertEqual(PRE_CLOSED_LEDGER_REVISION_ATTEMPTS, 8)
        self.assertEqual(TOP_ENTITY_REVISION_ATTEMPTS, 5)
        with self.assertRaises(ValueError):
            apply_extraction_revision_lock({"extraction_revision": False})
        with self.assertRaises(ValueError):
            apply_extraction_revision_lock({"disable_extraction_revision": True})
        with self.assertRaises(ValueError):
            apply_extraction_revision_lock({"skip_iter3_extraction": True})
        apply_extraction_revision_lock({"compare_one_shot": True})
        previous = os.environ.get(ONE_SHOT_ENV)
        try:
            os.environ.pop(ONE_SHOT_ENV, None)
            self.assertFalse(one_shot_extraction_enabled())
            self.assertTrue(extraction_revision_enabled({}))
            self.assertFalse(skip_extraction_judges({}))
        finally:
            if previous is None:
                os.environ.pop(ONE_SHOT_ENV, None)
            else:
                os.environ[ONE_SHOT_ENV] = previous

    def test_simple_main_domain_turns_extraction_revision_off(self) -> None:
        previous = os.environ.get(ONE_SHOT_ENV)
        try:
            os.environ.pop(ONE_SHOT_ENV, None)
            simple = apply_extraction_revision_lock({"execution_profile": "simple_main"})
            self.assertFalse(simple["extraction_revision"])
            self.assertFalse(extraction_revision_enabled(simple))
            self.assertTrue(skip_extraction_judges(simple))
            medical = apply_extraction_revision_lock({"ontology": "medical"})
            self.assertFalse(medical["extraction_revision"])
            self.assertTrue(
                apply_extraction_revision_lock(
                    {"execution_profile": "simple_extension"}
                )["extraction_revision"]
            )
            self.assertTrue(
                apply_extraction_revision_lock(
                    {"execution_profile": "complex_main"}
                )["extraction_revision"]
            )
            with self.assertRaises(ValueError):
                apply_extraction_revision_lock(
                    {"execution_profile": "complex_main", "extraction_revision": False}
                )
        finally:
            if previous is None:
                os.environ.pop(ONE_SHOT_ENV, None)
            else:
                os.environ[ONE_SHOT_ENV] = previous

    def test_one_shot_ablation_is_env_only(self) -> None:
        import os

        previous = os.environ.get(ONE_SHOT_ENV)
        try:
            os.environ[ONE_SHOT_ENV] = "1"
            self.assertTrue(one_shot_extraction_enabled())
            self.assertTrue(EXTRACTION_REVISION_LOCKED)
            self.assertEqual(MAIN_EXTRACTION_REVISION_ATTEMPTS, 5)
            from src.extraction_runtime.locked_mechanisms import payload_retry_attempts

            self.assertEqual(payload_retry_attempts(), MAIN_EXTRACTION_REVISION_ATTEMPTS)
        finally:
            if previous is None:
                os.environ.pop(ONE_SHOT_ENV, None)
            else:
                os.environ[ONE_SHOT_ENV] = previous

    def test_resume_skip_requires_revision_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "iter3_hints_MOP.txt"
            artifact.write_text("SEMANTIC_HINTS_V1\ndraft", encoding="utf-8")
            self.assertFalse(extraction_revision_complete(artifact))
            self.assertFalse(skip_if_already_revised("hints", artifact))
            write_extraction_revision_receipt(
                artifact,
                attempts=1,
                max_attempts=5,
                accepted=True,
                judges=["tbox_contract"],
            )
            self.assertTrue(extraction_revision_complete(artifact))
            self.assertTrue(skip_if_already_revised("hints", artifact))

    def test_repeated_judge_feedback_is_a_stall(self) -> None:
        first = revision_feedback_fingerprint(
            "FIELD_NOT_ACCEPTED_BY_CLASS: Add.hasAlternativeNames [source: foo]"
        )
        second = revision_feedback_fingerprint(
            "FIELD_NOT_ACCEPTED_BY_CLASS: Add.hasAlternativeNames [source: bar]"
        )
        self.assertEqual(first, second)
        self.assertFalse(same_revision_feedback(first, None))
        self.assertTrue(same_revision_feedback(second, first))
        self.assertFalse(
            same_revision_feedback(
                revision_feedback_fingerprint("different judge text"),
                first,
            )
        )

    def test_semantic_main_extraction_skips_json_contract_critic(self) -> None:
        from src.extraction_runtime.steps.main_extraction.run import (
            _extraction_judges_run,
        )

        self.assertEqual(
            _extraction_judges_run(semantic_mode=True, closed_ledger_projection=False),
            ["format"],
        )
        self.assertEqual(
            _extraction_judges_run(semantic_mode=True, closed_ledger_projection=True),
            ["format", "operation_projection"],
        )
        self.assertEqual(
            _extraction_judges_run(semantic_mode=False, closed_ledger_projection=True),
            ["format", "hint_payload", "semantic_projection", "tbox_contract"],
        )


class KgRevisionLockTests(unittest.TestCase):
    def test_kg_revision_cannot_be_enabled(self) -> None:
        self.assertTrue(KG_REVISION_LOCKED_OFF)
        self.assertEqual(KG_MAX_ATTEMPTS, 1)
        self.assertEqual(KG_HINT_REVISION_MAX_ATTEMPTS, 0)
        locked = apply_kg_revision_lock({})
        self.assertFalse(locked["kg_revision"])
        self.assertTrue(locked["disable_kg_revisions"])
        self.assertEqual(locked["kg_max_attempts"], 1)
        self.assertEqual(locked["kg_hint_revision_max_attempts"], 0)
        self.assertEqual(locked["post_publish_structural_retries"], 0)
        self.assertFalse(locked["continuity_audit"]["enabled"])
        with self.assertRaises(ValueError):
            apply_kg_revision_lock({"kg_hint_revision_max_attempts": 2})
        with self.assertRaises(ValueError):
            apply_kg_revision_lock({"kg_max_attempts": 4})
        with self.assertRaises(ValueError):
            apply_kg_revision_lock({"disable_kg_revisions": False})
        with self.assertRaises(ValueError):
            apply_kg_revision_lock({"continuity_audit": {"enabled": True}})

    def test_pipeline_and_ox_protocol_carry_the_lock(self) -> None:
        config = lock_revision_policy({"steps": ["main_ontology_extractions"]})
        self.assertTrue(config["extraction_revision"])
        self.assertFalse(config["kg_revision"])
        apply_to_pipeline_config(config, "generic-strict")
        self.assertTrue(config["extraction_revision"])
        self.assertFalse(config["kg_revision"])
        spec = resolve_protocol("generic-strict")
        self.assertTrue(spec.extraction_revision)
        self.assertFalse(spec.kg_revision)
        summary = ox_summary_fields(spec)
        self.assertTrue(summary["extraction_revision"])
        self.assertFalse(summary["kg_revision"])
        medical = apply_to_pipeline_config(
            {"ontology": "medical", "steps": ["main_kg_building"]},
            "generic-strict",
        )
        self.assertFalse(medical["extraction_revision"])
        self.assertEqual(medical["execution_profile"], "simple_main")
        self.assertFalse(medical["kg_revision"])


if __name__ == "__main__":
    unittest.main()
