from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.pipelines.utils.hint_size_dedup import (
    collapse_consecutive_repeats,
    maybe_dedup_oversized_hint,
    resolve_hint_size_threshold,
)


def _ledger(*, repeats: int, extra_steps: int = 1) -> str:
    names = ["diethyl ether", "ethoxyethane", "ether"]
    names.extend(["Etherum [WHO-IP LATIN]"] * repeats)
    lines = [
        "SEMANTIC_HINTS_V1",
        "",
        "StepType: Add",
        "hasOrder: 1",
        f"hasAddedChemicalInput: diethyl ether",
        f" - hasAlternativeNames: {'; '.join(names)}",
    ]
    for index in range(extra_steps):
        lines.extend(
            [
                "",
                "StepType: HeatChill",
                f"hasOrder: {index + 2}",
                "hasStepDuration: 12 hours",
            ]
        )
    return "\n".join(lines) + "\n"


class TestHintSizeDedup(unittest.TestCase):
    def test_below_threshold_skips_llm(self) -> None:
        text = _ledger(repeats=2)
        called = []

        def invoke(*args, **kwargs):
            called.append(True)
            return text

        result = maybe_dedup_oversized_hint(
            text,
            threshold=len(text) + 10,
            invoke=invoke,
        )
        self.assertFalse(result.applied)
        self.assertEqual(result.reason, "below_threshold")
        self.assertEqual(result.text, text)
        self.assertFalse(called)

    def test_alarm_rewrites_when_structure_holds(self) -> None:
        original = _ledger(repeats=200)
        cleaned = _ledger(repeats=1)

        result = maybe_dedup_oversized_hint(
            original,
            threshold=200,
            invoke=lambda *args, **kwargs: cleaned,
        )
        self.assertTrue(result.applied)
        self.assertEqual(result.reason, "llm_dedup")
        self.assertEqual(result.text, cleaned.strip())
        self.assertLess(result.after_chars, result.before_chars)

    def test_rejects_rewrite_that_drops_steps(self) -> None:
        original = _ledger(repeats=200, extra_steps=2)
        truncated = "\n".join(original.splitlines()[:6]) + "\n"

        result = maybe_dedup_oversized_hint(
            original,
            threshold=200,
            invoke=lambda *args, **kwargs: truncated,
        )
        self.assertTrue(result.applied)
        self.assertEqual(result.reason, "fallback_consecutive_collapse")
        self.assertIn("Etherum [WHO-IP LATIN]", result.text)
        self.assertEqual(result.text.count("Etherum [WHO-IP LATIN]"), 1)
        self.assertIn("hasOrder: 3", result.text)

    def test_keeps_nonconsecutive_duplicates(self) -> None:
        text = (
            "SEMANTIC_HINTS_V1\n\n"
            "StepType: Add\n"
            "hasOrder: 1\n"
            " - hasAlternativeNames: alpha; beta; alpha\n"
        )
        collapsed = collapse_consecutive_repeats(text)
        self.assertIn("alpha; beta; alpha", collapsed)

    def test_persists_backup_when_applied(self) -> None:
        original = _ledger(repeats=200)
        cleaned = _ledger(repeats=1)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "iter3_hints_example.txt"
            path.write_text(original, encoding="utf-8")
            result = maybe_dedup_oversized_hint(
                original,
                threshold=200,
                invoke=lambda *args, **kwargs: cleaned,
                artifact_path=str(path),
            )
            self.assertTrue(result.applied)
            self.assertEqual(path.read_text(encoding="utf-8"), cleaned.strip())
            backup = Path(str(path) + ".pre_size_dedup.txt")
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            sidecar = Path(str(path) + ".size_dedup.json")
            self.assertTrue(sidecar.is_file())

    def test_threshold_env_override(self) -> None:
        self.assertEqual(resolve_hint_size_threshold(4096), 4096)
        self.assertEqual(resolve_hint_size_threshold("nope"), 32768)


if __name__ == "__main__":
    unittest.main()
