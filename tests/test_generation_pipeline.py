from __future__ import annotations

import json
import unittest
from pathlib import Path


def _path_ok(path: Path) -> bool:
    return path.exists()


class TestGenerationPipelineSanity(unittest.TestCase):
    def test_meta_task_config_exists_and_has_main_and_extensions(self):
        p = Path("configs/meta_task/meta_task_config.json")
        self.assertTrue(p.exists(), f"Missing {p}")
        cfg = json.loads(p.read_text(encoding="utf-8"))
        self.assertIn("ontologies", cfg)
        self.assertIn("main", cfg["ontologies"])
        self.assertIn("extensions", cfg["ontologies"])

    def test_extension_meta_task_configs_keep_ccdc_enabled(self):
        p = Path("configs/meta_task/meta_task_config.json")
        cfg = json.loads(p.read_text(encoding="utf-8"))
        extensions = {
            ext["name"]: ext
            for ext in cfg.get("ontologies", {}).get("extensions", [])
            if isinstance(ext, dict) and ext.get("name")
        }
        self.assertIn("ccdc", extensions["ontomops"].get("mcp_list", []))
        self.assertIn("ccdc", extensions["ontospecies"].get("mcp_list", []))

    @unittest.skipUnless(
        _path_ok(Path("ai_generated_contents_candidate/iterations/ontomops/iterations.json")),
        "Candidate iterations missing; run scripts/rebuild_pipeline_artifacts.sh to materialize ai_generated_contents_candidate/",
    )
    def test_candidate_extension_iterations_keep_ccdc_enabled(self):
        for ontology in ("ontomops", "ontospecies"):
            p = Path(f"ai_generated_contents_candidate/iterations/{ontology}/iterations.json")
            self.assertTrue(p.exists(), f"Missing {p}")
            cfg = json.loads(p.read_text(encoding="utf-8"))
            iterations = cfg.get("iterations") or []
            self.assertTrue(iterations, f"No iterations in {p}")
            first = iterations[0]
            tool_lists = (
                first.get("extraction_mcp_tools")
                or first.get("mcp_tools")
                or []
            )
            self.assertIn("ccdc", first.get("mcp_tools", []))
            self.assertIn("ccdc", tool_lists)

    @unittest.skipUnless(
        _path_ok(Path("ai_generated_contents/prompts/ontosynthesis/EXTRACTION_ITER_1.md")),
        "Production prompt tree missing; run bootstrap + artifact promotion after rebuild.",
    )
    def test_runtime_prompts_and_iterations_exist_for_main_ontology(self):
        # Runtime pipeline consumes from ai_generated_contents/
        prompts_dir = Path("ai_generated_contents/prompts/ontosynthesis")
        iters_file = Path("ai_generated_contents/iterations/ontosynthesis/iterations.json")
        self.assertTrue(prompts_dir.exists(), f"Missing {prompts_dir}")
        self.assertTrue(iters_file.exists(), f"Missing {iters_file}")
        # The top-entity extraction step hardcodes ITER1 prompt path
        self.assertTrue((prompts_dir / "EXTRACTION_ITER_1.md").exists())
        # The top-entity KG building step hardcodes KG_BUILDING_ITER_1 prompt path
        self.assertTrue((prompts_dir / "KG_BUILDING_ITER_1.md").exists())

    def test_run_created_mcp_points_to_generated_entrypoint_module(self):
        # Runtime pipeline uses configs/run_created_mcp.json by default
        p = Path("configs/run_created_mcp.json")
        self.assertTrue(p.exists(), f"Missing {p}")
        cfg = json.loads(p.read_text(encoding="utf-8"))
        self.assertIn("llm_created_mcp", cfg)
        args = cfg["llm_created_mcp"].get("args") or []
        self.assertGreaterEqual(len(args), 2)
        self.assertEqual(args[:1], ["-m"])
        self.assertEqual(args[1], "ai_generated_contents_candidate.scripts.ontosynthesis.main")

    @unittest.skipUnless(
        _path_ok(Path("ai_generated_contents_candidate/scripts/ontosynthesis/main.py")),
        "Candidate MCP server missing; run scripts/rebuild_pipeline_artifacts.sh",
    )
    def test_generated_mcp_entrypoint_is_importable(self):
        # This should be importable without contacting any external services.
        import ai_generated_contents_candidate.scripts.ontosynthesis.main as m  # noqa: F401

        self.assertIsNotNone(getattr(m, "mcp", None))


if __name__ == "__main__":
    unittest.main()


