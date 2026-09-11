"""One-shot skips LLM judges, not empty/invalid payload retries."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.extraction_runtime.locked_mechanisms import (
    MAIN_EXTRACTION_REVISION_ATTEMPTS,
    ONE_SHOT_ENV,
    payload_retry_attempts,
)
from src.extraction_runtime.steps.main_extraction.prompts import (
    REF_ENTITY_REPRESENTATION,
)
from src.extraction_runtime.steps.main_extraction.run import (
    _run_extraction_with_judges,
)

VALID_HINTS = (
    '{"entities":[{"ref":"MetalOrganicPolyhedron-1","class":"MetalOrganicPolyhedron",'
    '"label":"MOP-1","datatype_properties":{}}],"relations":[]}'
)


class ExtractionPayloadRetryTests(unittest.TestCase):
    def test_payload_retry_budget_is_not_ablated(self) -> None:
        previous = os.environ.get(ONE_SHOT_ENV)
        try:
            os.environ[ONE_SHOT_ENV] = "1"
            self.assertEqual(payload_retry_attempts(), MAIN_EXTRACTION_REVISION_ATTEMPTS)
        finally:
            if previous is None:
                os.environ.pop(ONE_SHOT_ENV, None)
            else:
                os.environ[ONE_SHOT_ENV] = previous

    def test_one_shot_retries_empty_then_keeps_valid_payload(self) -> None:
        previous = os.environ.get(ONE_SHOT_ENV)
        os.environ[ONE_SHOT_ENV] = "1"
        fake = AsyncMock(
            side_effect=[
                ("", {}),
                ("", {}),
                (VALID_HINTS, {}),
            ]
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                hint_file = Path(tmp) / "ontomops_iter1_hints_MOP.txt"
                with patch(
                    "src.extraction_runtime.steps.main_extraction.run._run_extraction_llm_or_agent",
                    fake,
                ):
                    text = asyncio.run(
                        _run_extraction_with_judges(
                            prompt="extract",
                            prompt_template="template",
                            model_name="openai/gpt-4.1",
                            use_agent=False,
                            mcp_set_name=None,
                            mcp_tools=None,
                            representation=REF_ENTITY_REPRESENTATION,
                            source_text="paper body",
                            closed_ledger_revision=False,
                            hint_file=hint_file,
                            extraction_validation=None,
                            entity_label="MOP-1",
                            entity_uri="",
                            iter_num=1,
                            accumulated_hints="",
                        )
                    )
                self.assertEqual(fake.await_count, 3)
                self.assertIn("MetalOrganicPolyhedron-1", text)
                receipt = hint_file.with_name(hint_file.name + ".extraction_revision.json")
                self.assertTrue(receipt.is_file())
        finally:
            if previous is None:
                os.environ.pop(ONE_SHOT_ENV, None)
            else:
                os.environ[ONE_SHOT_ENV] = previous

    def test_one_shot_retries_invalid_json_then_keeps_valid_payload(self) -> None:
        previous = os.environ.get(ONE_SHOT_ENV)
        os.environ[ONE_SHOT_ENV] = "1"
        fake = AsyncMock(
            side_effect=[
                ("this is not extraction json", {}),
                (VALID_HINTS, {}),
            ]
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                hint_file = Path(tmp) / "ontomops_iter1_hints_MOP.txt"
                with patch(
                    "src.extraction_runtime.steps.main_extraction.run._run_extraction_llm_or_agent",
                    fake,
                ):
                    text = asyncio.run(
                        _run_extraction_with_judges(
                            prompt="extract",
                            prompt_template="template",
                            model_name="openai/gpt-4.1",
                            use_agent=False,
                            mcp_set_name=None,
                            mcp_tools=None,
                            representation=REF_ENTITY_REPRESENTATION,
                            source_text="paper body",
                            closed_ledger_revision=False,
                            hint_file=hint_file,
                            extraction_validation=None,
                            entity_label="MOP-1",
                            entity_uri="",
                            iter_num=1,
                            accumulated_hints="",
                        )
                    )
            self.assertEqual(fake.await_count, 2)
            self.assertIn("MOP-1", text)
        finally:
            if previous is None:
                os.environ.pop(ONE_SHOT_ENV, None)
            else:
                os.environ[ONE_SHOT_ENV] = previous
