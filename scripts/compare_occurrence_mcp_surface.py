"""Compare OntoSyn occurrence MCP public surfaces. No LLM."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def _defs(path: Path) -> dict[str, ast.arguments]:
    if not path.is_file():
        return {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, ast.arguments] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith(
            ("create_", "link_")
        ):
            out[node.name] = node.args
    return out


def _arg_names(args: ast.arguments) -> list[str]:
    names = [item.arg for item in args.args if item.arg != "self"]
    names.extend(item.arg for item in args.kwonlyargs)
    return names


def _required(args: ast.arguments) -> list[str]:
    positional = [item.arg for item in args.args if item.arg != "self"]
    defaults = list(args.defaults)
    required = positional[: max(0, len(positional) - len(defaults))]
    for item, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        if default is None:
            required.append(item.arg)
    return required


def surface(root: Path) -> dict[str, object]:
    ops = (
        root
        / "scripts"
        / "ontosynthesis"
        / "ontosynthesis_occurrence_operations.py"
    )
    defs = _defs(ops)
    creates = sorted(name for name in defs if name.startswith("create_"))
    links = sorted(name for name in defs if name.startswith("link_"))
    om2 = root / "scripts" / "ontosynthesis" / "_fixed_om2_runtime.py"
    reuse_path = root / "derived_inputs" / "ontosynthesis" / "reuse_policy.json"
    reuse_locals: dict[str, bool] = {}
    if reuse_path.is_file():
        payload = json.loads(reuse_path.read_text(encoding="utf-8"))
        for item in payload.get("classes") or []:
            if isinstance(item, dict) and item.get("class_local"):
                reuse_locals[str(item["class_local"])] = item.get("reusable") is True
    return {
        "root": str(root),
        "create": creates,
        "link": links,
        "has_om2": om2.is_file(),
        "has_crystallize": "create_Crystallize" in creates,
        "has_create_document_context": "create_DocumentContext" in creates,
        "has_link_document_context": "link_hasDocumentContext" in links,
        "has_link_equipment": "link_hasEquipment" in links,
        "chemical_output_required": _required(defs["create_ChemicalOutput"])
        if "create_ChemicalOutput" in defs
        else [],
        "chemical_output_args": _arg_names(defs["create_ChemicalOutput"])
        if "create_ChemicalOutput" in defs
        else [],
        "document_context_reusable": reuse_locals.get("DocumentContext"),
        "lab_equipment_reusable": reuse_locals.get("LabEquipment"),
        "equipment_reusable": reuse_locals.get("Equipment"),
    }


def _delta(left: list[str], right: list[str]) -> dict[str, list[str]]:
    a, b = set(left), set(right)
    return {
        "only_left": sorted(a - b),
        "only_right": sorted(b - a),
        "shared": sorted(a & b),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    left = surface(Path(args.left))
    right = surface(Path(args.right))
    report = {
        "left": left,
        "right": right,
        "create": _delta(left["create"], right["create"]),
        "link": _delta(left["link"], right["link"]),
    }
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print(f"LEFT  {left['root']}")
    print(f"RIGHT {right['root']}")
    print(f"create only-left  {report['create']['only_left']}")
    print(f"create only-right {report['create']['only_right']}")
    print(f"link only-left    {report['link']['only_left']}")
    print(f"link only-right   {report['link']['only_right']}")
    for key in (
        "has_om2",
        "has_crystallize",
        "has_create_document_context",
        "has_link_document_context",
        "has_link_equipment",
        "document_context_reusable",
        "lab_equipment_reusable",
        "equipment_reusable",
        "chemical_output_required",
        "chemical_output_args",
    ):
        print(f"{key:32} L={left[key]!r} R={right[key]!r}")


if __name__ == "__main__":
    main()
