#!/usr/bin/env python3
"""
Entity part generation — real LLM / “agent” smoke (not pytest, not mocked).

Runs one `generate_entity_part_script` call against the live OpenAI-compatible
client (same env as `llm_smoke_test.py`: REMOTE_API_KEY / OPENAI_API_KEY, optional
REMOTE_BASE_URL). Uses a minimal on-disk base script and optional concise override
so a clean clone can still run without `ai_generated_contents_candidate/`.

Example (from repo root, PYTHONPATH=.):

  python -m src.agents.scripts_and_prompts_generation.entity_part_agent_smoke \\
    --model gpt-4o --ontology-ttl data/ontologies/ontospecies-subgraph.ttl

Success: exit 0 and printed path to `*_creation_entities_1.py`; failure: non-zero.
"""
from __future__ import annotations

import argparse
import asyncio
import ast
import re
import sys
from pathlib import Path

# Repo root on sys.path when run as -m
from src.agents.scripts_and_prompts_generation.direct_script_generation import (
    _ontology_uses_om2_units,
    _render_namespaces_from_config,
    extract_concise_ontology_structure,
    generate_entity_part_script,
)


def _write_minimal_base(path: Path, namespace_map: dict[str, str], uses_om2: bool) -> None:
    lines: list[str] = ["from rdflib.namespace import Namespace\n\n"]
    for k, u in namespace_map.items():
        if u:
            lines.append(f'{k} = Namespace("{u}")\n')
    lines.extend(
        [
            "\n\ndef _guard_noncheck(f):\n    return f\n",
            "def _format_error(*a, **k):\n    return '{}'\n",
            "def _format_success_json(*a, **k):\n    return '{}'\n",
        ]
    )
    if uses_om2:
        lines.extend(
            [
                "OM2_UNIT_MAP = {}\n",
                "def _resolve_om2_unit(_u):\n    raise ValueError('unit')\n",
                "def _find_or_create_om2_quantity(_g, **kwargs):\n    return None\n",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def _resolve_concise(ontology_name: str, path_arg: str | None) -> tuple[str, str]:
    if path_arg:
        p = Path(path_arg)
        return p.read_text(encoding="utf-8"), str(p.resolve())
    candidate = (
        Path("ai_generated_contents_candidate")
        / "ontology_structures"
        / f"{ontology_name}_concise.md"
    )
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8"), str(candidate.resolve())
    return (
        "# Minimal placeholder (no candidate concise.md found). OK for smoke.\n",
        "inline-placeholder",
    )


def _validate_device_typed_if_expected(
    src: str, *, expect_device: bool
) -> tuple[bool, str]:
    if not expect_device:
        return True, ""
    if "NAMESPACE['Device']" in src or 'NAMESPACE["Device"]' in src:
        return True, ""
    # Accept attribute form sometimes emitted by the model
    if re.search(r"\bNAMESPACE\.Device\b", src):
        return True, ""
    return False, "Expected explicit typing for parent class Device (e.g. NAMESPACE['Device'])."


async def _run(args: argparse.Namespace) -> int:
    ttl = Path(args.ontology_ttl).resolve()
    if not ttl.is_file():
        print(f"[FAIL] Ontology not found: {ttl}", file=sys.stderr)
        return 2

    oname = args.ontology_name
    out_root = Path(args.out_dir).resolve()
    out_dir = out_root / "scripts" / oname
    out_dir.mkdir(parents=True, exist_ok=True)

    uses_om2 = _ontology_uses_om2_units(str(ttl), oname)
    concise = extract_concise_ontology_structure(str(ttl), include_om2_mock=uses_om2)
    ns_map = _render_namespaces_from_config(concise)
    structures = concise.get("class_structures") or {}
    if args.cls not in structures:
        print(
            f"[FAIL] Class {args.cls!r} not in T-Box. Pick a class from the ontology.",
            file=sys.stderr,
        )
        return 2

    base_path = out_dir / f"{oname}_creation_base.py"
    _write_minimal_base(base_path, ns_map, uses_om2)

    concise_body, concise_src = _resolve_concise(oname, args.concise_file)
    print(f"[INFO] Concise source: {concise_src}")
    print(f"[INFO] Output dir: {out_dir}")
    print(f"[INFO] Model: {args.model} | OM-2 mode: {uses_om2}")
    print("[INFO] Calling generate_entity_part_script (real API)…")

    path = await generate_entity_part_script(
        ontology_path=str(ttl),
        ontology_name=oname,
        part_number=1,
        classes_to_generate=[args.cls],
        output_dir=str(out_dir),
        base_script_path=str(base_path),
        checks_script_path=str(out_dir / f"{oname}_creation_checks.py"),
        relationships_script_path=str(out_dir / f"{oname}_creation_relationships.py"),
        model_name=args.model,
        max_retries=max(1, int(args.max_retries)),
        concise_content_override=concise_body,
    )
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    try:
        ast.parse(text)
    except SyntaxError as e:
        print(f"[FAIL] Written file is not valid Python: {e}", file=sys.stderr)
        return 1

    parents = (structures.get(args.cls, {}) or {}).get("parent_classes") or []
    expect_dev = "Device" in parents
    ok, err = _validate_device_typed_if_expected(text, expect_device=expect_dev)
    if not ok:
        print(f"[FAIL] {err}", file=sys.stderr)
        return 1

    print(f"[OK] Wrote: {p.resolve()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Real-LLM smoke for one entity part (no mocks, not a unit test).",
    )
    ap.add_argument(
        "--ontology-ttl",
        default="data/ontologies/ontospecies-subgraph.ttl",
        help="Path to ontology file (default: ontospecies subgraph)",
    )
    ap.add_argument(
        "--ontology-name",
        default="ontospecies",
        help="Module prefix for *_creation_*.py (default: ontospecies)",
    )
    ap.add_argument(
        "--class",
        dest="cls",
        default="ElementalAnalysisDevice",
        help="Local class name to implement in this part (default: ElementalAnalysisDevice)",
    )
    ap.add_argument("--model", default="gpt-4o", help="Model id for the API (default: gpt-4o)")
    ap.add_argument("--max-retries", type=int, default=3, help="Retries inside generator (default: 3)")
    ap.add_argument(
        "--out-dir",
        default="tmp/entity_part_agent_smoke",
        help="Root output directory (default: tmp/entity_part_agent_smoke, gitignored via /tmp)",
    )
    ap.add_argument(
        "--concise-file",
        default=None,
        help="Optional path to a concise .md; else use candidate file or placeholder",
    )
    args = ap.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
