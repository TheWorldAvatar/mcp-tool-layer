from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.mcp_servers.agentic_generation_workspace import main as workspace


class TestAgenticGenerationWorkspaceMCP(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_file = Path("tmp/agentic_generation/tests/workspace.txt")
        self.tmp_file.parent.mkdir(parents=True, exist_ok=True)
        if self.tmp_file.exists():
            self.tmp_file.unlink()

    def tearDown(self) -> None:
        if self.tmp_file.exists():
            self.tmp_file.unlink()

    def test_write_read_patch_and_diff_within_allowed_root(self) -> None:
        written = json.loads(workspace.write_workspace_file(str(self.tmp_file), "alpha\n"))
        self.assertTrue(written["ok"])

        read = json.loads(workspace.read_workspace_file(str(self.tmp_file)))
        self.assertEqual(read["content"], "alpha\n")

        patch = """@@
-alpha
+beta
"""
        patched = json.loads(workspace.apply_unified_patch(str(self.tmp_file), patch))
        self.assertEqual(patched["hunks_applied"], 1)
        self.assertEqual(self.tmp_file.read_text(encoding="utf-8"), "beta\n")

        diff = workspace.show_workspace_diff(str(self.tmp_file))
        self.assertIn("+beta", diff)

    def test_rejects_writes_outside_isolated_roots(self) -> None:
        with self.assertRaises(workspace.WorkspaceSafetyError):
            workspace.write_workspace_file("README.md", "not allowed")

    def test_validation_command_allow_list(self) -> None:
        with self.assertRaises(workspace.WorkspaceSafetyError):
            workspace.run_allowed_validation_command("python -c \"print('blocked')\"")
        allowed = json.loads(
            workspace.run_allowed_validation_command(
                "python -m py_compile tmp/agentic_generation/tests/compile_me.py"
            )
        )
        # The command shape is allowed even if the file does not exist.
        self.assertFalse(allowed["ok"])


if __name__ == "__main__":
    unittest.main()
