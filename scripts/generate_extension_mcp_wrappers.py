#!/usr/bin/env python3
"""
Generate candidate extension MCP entrypoints for the pipeline.

Why:
- The pipeline can be rewired to load extension MCP servers from the candidate tree:
    ai_generated_contents_candidate.scripts.<ontology>.main
- In this repo, the extension MCP servers are implemented (and maintained) in `src/`:
    - src/ontomops_extension/main.py
    - src/ontospecies_extension/main.py
- So we generate thin wrappers under `ai_generated_contents_candidate/scripts/` that
  simply expose those servers via stdio.
"""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _wrapper(module: str, transport: str = "stdio") -> str:
    return (
        '"""\n'
        "Candidate extension MCP entrypoint.\n\n"
        "This is a thin wrapper around a stable implementation under `src/`.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        f"from {module} import mcp\n\n\n"
        'if __name__ == "__main__":\n'
        f"    mcp.run(transport={transport!r})\n"
    )


def main() -> int:
    # 1) Write wrappers
    cand = REPO_ROOT / "ai_generated_contents_candidate" / "scripts"

    _write(cand / "__init__.py", '"""Candidate MCP scripts package."""\n')
    _write(cand / "ontomops" / "__init__.py", '"""Candidate MCP scripts for ontomops extension."""\n')
    _write(cand / "ontospecies" / "__init__.py", '"""Candidate MCP scripts for ontospecies extension."""\n')

    _write(cand / "ontomops" / "main.py", _wrapper("src.ontomops_extension.main"))
    _write(cand / "ontospecies" / "main.py", _wrapper("src.ontospecies_extension.main"))

    # 2) Update configs/extension.json to point to candidate modules
    ext_path = REPO_ROOT / "configs" / "extension.json"
    ext = json.loads(ext_path.read_text(encoding="utf-8"))

    ext.setdefault("mops_extension", {})
    ext["mops_extension"].update(
        {"command": "python", "args": ["-m", "ai_generated_contents_candidate.scripts.ontomops.main"], "transport": "stdio"}
    )

    ext.setdefault("ontospecies_extension", {})
    ext["ontospecies_extension"].update(
        {
            "command": "python",
            "args": ["-m", "ai_generated_contents_candidate.scripts.ontospecies.main"],
            "transport": "stdio",
        }
    )

    ext_path.write_text(json.dumps(ext, indent=2) + "\n", encoding="utf-8")

    print("[OK] Wrote candidate extension wrappers.")
    print("[OK] Updated configs/extension.json to use candidate wrappers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

