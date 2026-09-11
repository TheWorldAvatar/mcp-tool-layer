"""Batch layout for generated prompt packages. No LLM calls."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from models.generated_layout import (
    find_generation_run,
    mint_generation_run,
    resolve_generated_package_root,
    resolve_generation_output_root,
    write_current_pointer,
)
from src.extraction_prompt_generation.cli import build_parser, campaign_ontology_order
from src.extraction_prompt_generation.config.domain_config import (
    apply_generation_model_override,
    load_domain_generation_config,
)
from src.extraction_prompt_generation.paths import default_domain_config_path
from src.extraction_runtime.cli import build_parser as build_runtime_parser


class GeneratedLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("TWA_GENERATED_ARTIFACT_ROOT", None)
        os.environ.pop("TWA_GENERATION_MODEL", None)
        self.addCleanup(self._env.stop)

    def test_mint_and_current_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "generated"
            repo = Path(tmp)
            root = mint_generation_run(
                tag="gpt5_r1",
                model="gpt-5",
                home=home,
                repository=repo,
                run_id="20260906_000000_gpt5_r1",
            )
            self.assertEqual(root.name, "20260906_000000_gpt5_r1")
            self.assertTrue((root / "batch.json").is_file())
            self.assertEqual(
                resolve_generated_package_root(
                    home=home, repository=repo, env_override=False
                ),
                root,
            )
            self.assertEqual(
                find_generation_run("gpt5_r1", home=home, repository=repo),
                root,
            )

    def test_tag_reuses_unique_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "generated"
            repo = Path(tmp)
            first = mint_generation_run(
                tag="kimi_k3",
                model="kimi-k3",
                home=home,
                repository=repo,
                run_id="20260906_010000_kimi_k3",
                set_current=False,
            )
            reused = resolve_generation_output_root(
                tag="kimi_k3",
                model="kimi-k3",
                home=home,
                repository=repo,
                set_current=False,
            )
            self.assertEqual(reused, first)

    def test_env_home_prefers_current_over_legacy_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            home = repo / "generated"
            (home / "prompts").mkdir(parents=True)
            run = mint_generation_run(
                tag="gpt5_r2",
                model="gpt-5",
                home=home,
                repository=repo,
                run_id="20260906_020000_gpt5_r2",
            )
            (run / "prompts").mkdir()
            with patch.dict(os.environ, {"TWA_GENERATED_ARTIFACT_ROOT": str(home)}, clear=False):
                resolved = resolve_generated_package_root(home=home, repository=repo)
            self.assertEqual(resolved, run)
            self.assertEqual(
                find_generation_run("legacy", home=home, repository=repo),
                home.resolve(),
            )

    def test_generation_model_override_is_ignored(self) -> None:
        from models.locked_llm import LOCKED_GENERATION_MODEL

        models = apply_generation_model_override(
            {
                "prompt_generation": "gpt-5",
                "runtime_extraction": "gpt-4.1",
                "runtime_kg_building": "gpt-4o",
            },
            "kimi-k3",
        )
        self.assertEqual(models["prompt_generation"], LOCKED_GENERATION_MODEL)
        self.assertEqual(models["reuse_judgment"], LOCKED_GENERATION_MODEL)
        self.assertEqual(models["runtime_extraction"], "gpt-4.1")
        self.assertEqual(models["runtime_kg_building"], "gpt-4o")

    def test_domain_config_ignores_generation_model_override(self) -> None:
        from models.locked_llm import LOCKED_GENERATION_MODEL

        config = load_domain_generation_config(
            default_domain_config_path("ontosynthesis"),
            generation_model="kimi-k3",
        )
        self.assertEqual(config.models["prompt_generation"], LOCKED_GENERATION_MODEL)
        self.assertEqual(config.models["top_entity_planning"], LOCKED_GENERATION_MODEL)
        self.assertEqual(config.models["runtime_extraction"], "gpt-4.1-2025-04-14")

    def test_generation_cli_batch_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["--all-domains", "--tag", "gpt5_r1", "--model", "gpt-5", "--no-current"]
        )
        self.assertTrue(args.all_domains)
        self.assertEqual(args.tag, "gpt5_r1")
        self.assertEqual(args.model, "gpt-5")
        self.assertTrue(args.no_current)
        self.assertEqual(
            campaign_ontology_order()[:4],
            ["ontosynthesis", "ontomops", "ontospecies", "medical"],
        )

    def test_runtime_cli_selects_generation_batch(self) -> None:
        parser = build_runtime_parser()
        args = parser.parse_args(
            [
                "ontosynthesis",
                "--generation-run",
                "gpt5_r1",
                "--generated-root",
                "generated/runs/demo",
            ]
        )
        self.assertEqual(args.generation_run, "gpt5_r1")
        self.assertEqual(args.generated_root, "generated/runs/demo")


class IsolatedRuntimeRootTests(unittest.TestCase):
    def test_run_package_does_not_fall_back_to_legacy_generated(self) -> None:
        from src.extraction_runtime.artifact_root import resolve_generated_file

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            home = repo / "generated"
            legacy = home / "prompts" / "ontosynthesis"
            legacy.mkdir(parents=True)
            (legacy / "EXTRACTION_ITER_1.md").write_text("LEGACY", encoding="utf-8")
            run = home / "runs" / "20260906_030000_gpt5_r3"
            (run / "prompts" / "ontosynthesis").mkdir(parents=True)
            write_current_pointer(run, home=home, repository=repo)
            with patch(
                "src.extraction_runtime.artifact_root.generated_artifact_root",
                return_value=run,
            ), patch(
                "src.extraction_runtime.artifact_root.generated_home",
                return_value=home,
            ), patch(
                "src.extraction_runtime.artifact_root.repository_root",
                return_value=repo,
            ), patch.dict(os.environ, {"TWA_REQUIRE_GENERATED_ARTIFACT_ROOT": "0"}):
                missing = resolve_generated_file("prompts/ontosynthesis/EXTRACTION_ITER_1.md")
            self.assertEqual(missing, run / "prompts" / "ontosynthesis" / "EXTRACTION_ITER_1.md")
            self.assertFalse(missing.is_file())


if __name__ == "__main__":
    unittest.main()
