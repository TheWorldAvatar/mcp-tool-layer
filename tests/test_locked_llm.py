"""Welded GPT-5 / extraction sampling cannot be overridden."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from models.locked_llm import (
    LOCKED_EXTRACTION_MODEL,
    LOCKED_GENERATION_MODEL,
    LOCKED_LLM_SEED,
    apply_locked_sampling,
    canonicalize_chat_model,
    model_omits_temperature,
)
from models.LLMCreator import LLMCreator
from src.extraction_prompt_generation.generate.authoring_task import _generation_task
from src.extraction_runtime.models_map import get_extraction_model


class LockedLlmTests(unittest.TestCase):
    def test_unversioned_aliases_map_to_dated_snapshots(self) -> None:
        self.assertEqual(canonicalize_chat_model("gpt-5"), LOCKED_GENERATION_MODEL)
        self.assertEqual(
            canonicalize_chat_model("openai/gpt-5"),
            f"openai/{LOCKED_GENERATION_MODEL}",
        )
        self.assertEqual(canonicalize_chat_model("gpt-4.1"), LOCKED_EXTRACTION_MODEL)
        self.assertEqual(
            canonicalize_chat_model(LOCKED_GENERATION_MODEL),
            LOCKED_GENERATION_MODEL,
        )

    def test_gpt5_omits_temperature_and_seed_ignores_env(self) -> None:
        self.assertTrue(model_omits_temperature(LOCKED_GENERATION_MODEL))
        self.assertFalse(model_omits_temperature(LOCKED_EXTRACTION_MODEL))
        with patch.dict(os.environ, {"TWA_LLM_SEED": "99", "TWA_REASONING_EFFORT": "high"}):
            gpt5 = apply_locked_sampling(
                {"temperature": 1.2, "top_p": 0.9, "seed": 7},
                LOCKED_GENERATION_MODEL,
            )
            gpt41 = apply_locked_sampling(
                {"temperature": 1.2, "seed": 7},
                LOCKED_EXTRACTION_MODEL,
            )
        self.assertNotIn("temperature", gpt5)
        self.assertEqual(gpt5["seed"], LOCKED_LLM_SEED)
        self.assertEqual(gpt5["n"], 1)
        self.assertFalse(gpt5["streaming"])
        self.assertEqual(gpt41["temperature"], 0.0)
        self.assertEqual(gpt41["seed"], LOCKED_LLM_SEED)

    def test_llmcreator_canonicalizes_and_ignores_env_seed(self) -> None:
        with patch.dict(os.environ, {"TWA_LLM_SEED": "99"}):
            creator = LLMCreator(model="gpt-5")
        self.assertEqual(creator.model, LOCKED_GENERATION_MODEL)

    def test_extraction_model_map_ignores_env_path_and_aliases(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TWA_EXTRACTION_MODELS_PATH": "missing.json",
                "EXTRACTION_MODELS_PATH": "missing.json",
            },
        ):
            self.assertEqual(
                get_extraction_model("iter1_hints"),
                LOCKED_EXTRACTION_MODEL,
            )
            self.assertEqual(
                get_extraction_model("model:gpt-4.1"),
                LOCKED_EXTRACTION_MODEL,
            )
            self.assertEqual(
                get_extraction_model("model:gpt-5"),
                LOCKED_GENERATION_MODEL,
            )

    def test_generation_meta_prompt_is_the_authoring_task(self) -> None:
        self.assertTrue(callable(_generation_task))
        self.assertEqual(_generation_task.__module__, "src.extraction_prompt_generation.generate.authoring_task")


if __name__ == "__main__":
    unittest.main()
