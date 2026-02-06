#!/usr/bin/env python3
"""
Compare OntoSynthesis reference vs candidate scripts at a high-signal level.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _mcp_tool_names(main_path: str) -> set[str]:
    mod = ast.parse(Path(main_path).read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in mod.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "mcp" and dec.func.attr == "tool":
                    out.add(node.name)
            elif isinstance(dec, ast.Attribute) and isinstance(dec.value, ast.Name):
                if dec.value.id == "mcp" and dec.attr == "tool":
                    out.add(node.name)
    return out


def _contains_any(path: str, needles: list[str]) -> list[str]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return [n for n in needles if n in text]


def main() -> None:
    ref_main = "ai_generated_contents_reference/scripts/ontosynthesis/main.py"
    cand_main = "ai_generated_contents_candidate/scripts/ontosynthesis/main.py"

    ref_tools = _mcp_tool_names(ref_main)
    cand_tools = _mcp_tool_names(cand_main)

    missing = sorted(ref_tools - cand_tools)
    extra = sorted(cand_tools - ref_tools)

    print("== MCP tool names (main.py) ==")
    print("ref:", len(ref_tools))
    print("cand:", len(cand_tools))
    print("missing_from_candidate:", len(missing))
    for n in missing:
        print(" -", n)
    print("extra_in_candidate:", len(extra))
    for n in extra[:40]:
        print(" +", n)
    if len(extra) > 40:
        print(" + ...", len(extra) - 40, "more")

    print("\n== Entity OM-2 unit-table duplication check ==")
    needles = [
        "_TEMPERATURE_UNITS",
        "_PRESSURE_UNITS",
        "_DURATION_UNITS",
        "_VOLUME_UNITS",
        "_TEMPERATURE_RATE_UNITS",
        "_AMOUNT_FRACTION_UNITS",
        "_TEMPERATURE_UNIT_MAP",
        "_PRESSURE_UNIT_MAP",
        "_DURATION_UNIT_MAP",
        "_VOLUME_UNIT_MAP",
        "_TEMPERATURE_RATE_UNIT_MAP",
        "_AMOUNT_OF_SUBSTANCE_FRACTION_UNIT_MAP",
    ]
    for fn in [
        "ai_generated_contents_candidate/scripts/ontosynthesis/ontosynthesis_creation_entities_1.py",
        "ai_generated_contents_candidate/scripts/ontosynthesis/ontosynthesis_creation_entities_2.py",
    ]:
        hits = _contains_any(fn, needles)
        print(Path(fn).name, "->", ("OK" if not hits else f"FOUND {len(hits)} markers: {hits}"))


if __name__ == "__main__":
    main()


