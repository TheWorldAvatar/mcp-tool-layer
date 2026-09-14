"""Write a stdio launcher next to a generated occurrence MCP package."""

from __future__ import annotations

from pathlib import Path


def write_launch(output_root: Path, ontology_name: str) -> str:
    """Write `_launch_<ontology>_mcp.py` that imports the generated `main.mcp`."""
    path = Path(output_root) / f"_launch_{ontology_name}_mcp.py"
    root = Path(output_root).resolve()
    path.write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"ARTIFACT_ROOT = Path({str(root)!r})\n"
        "SCRIPTS_ROOT = ARTIFACT_ROOT / 'scripts'\n"
        "sys.path.insert(0, str(SCRIPTS_ROOT))\n"
        f"from {ontology_name}.main import mcp\n"
        'mcp.run(transport="stdio")\n',
        encoding="utf-8",
        newline="\n",
    )
    return str(path)
