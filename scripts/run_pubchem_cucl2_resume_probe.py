"""Resume one failed top entity after iter1: extraction 2/3/4 + main KG.

Copies 50307a45 out of the scored e2e runtime, keeps Iteration 1 artifacts,
wipes only the post-synthetic CuCl2 later-iteration files, then reruns with
the slim PubChem MCP.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC_PAPER = (
    REPO / "scenarios/mops/runs/20260820_eval30_presence-e2e-p6/runtime/50307a45"
)
SRC_RUNTIME = SRC_PAPER.parent
PROBE = REPO / "scenarios/mops/runs/20260820_pubchem_cucl2_resume_probe"
RUNTIME = PROBE / "runtime"
PAPER = RUNTIME / "50307a45"
MCP_CONFIG = REPO / "configs/test_mcp_config_ontosynthesis_20260820_pubchem_cucl2_resume_probe.json"
TARGET_SUBSTRING = "post-synthetic metallization"
PYTHON = r"C:\Users\xz378\AppData\Local\anaconda3\envs\mcp_layer\python.exe"


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_iter1_baseline(src_paper: Path, dst_paper: Path) -> list[str]:
    copied: list[str] = []
    for path in src_paper.iterdir():
        if path.is_file():
            _copy_file(path, dst_paper / path.name)
            copied.append(path.name)
    for relative in (
        "mcp_run/iter1_top_entities.json",
        "mcp_run/top_entity_identity_lock.json",
        "memory/top.ttl",
    ):
        src = src_paper / relative
        if src.is_file():
            _copy_file(src, dst_paper / relative)
            copied.append(relative)
    identity_dir = src_paper / "memory"
    if identity_dir.is_dir():
        for path in identity_dir.glob("*.identity.json"):
            _copy_file(path, dst_paper / "memory" / path.name)
            copied.append(f"memory/{path.name}")
    inherit_src = src_paper / "procedure_inheritance"
    inherit_dst = dst_paper / "procedure_inheritance"
    if inherit_src.is_dir():
        inherit_dst.mkdir(parents=True, exist_ok=True)
        for path in inherit_src.glob("*.json"):
            _copy_file(path, inherit_dst / path.name)
            copied.append(f"procedure_inheritance/{path.name}")
    return copied


def prepare(*, keep_runtime: bool = False) -> dict:
    from src.pipelines.main_kg_building.build import _safe_name as kg_safe
    from src.pipelines.main_ontology_extractions.extract import _safe_name as extract_safe
    from src.pipelines.utils.top_entity_identity import entity_scope_name

    PROBE.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if keep_runtime:
        if not PAPER.exists():
            raise FileNotFoundError(f"probe runtime missing: {PAPER}")
        copied = []
    else:
        if PAPER.exists():
            shutil.rmtree(PAPER)
        copied = _copy_iter1_baseline(SRC_PAPER, PAPER)
        doi_map = SRC_RUNTIME / "doi_to_hash.json"
        if doi_map.is_file():
            shutil.copy2(doi_map, RUNTIME / "doi_to_hash.json")
    (PAPER / ".main_ontology_extractions_done").unlink(missing_ok=True)
    (PAPER / ".main_kg_building_done").unlink(missing_ok=True)

    entities = json.loads((PAPER / "mcp_run" / "iter1_top_entities.json").read_text(encoding="utf-8"))
    target = next(
        entity
        for entity in entities
        if TARGET_SUBSTRING in str(entity.get("label") or "")
    )
    label = str(target["label"])
    uri = str(target.get("uri") or "")
    names = {
        "label": label,
        "uri": uri,
        "extract_safe": extract_safe(label),
        "kg_safe": kg_safe(label),
        "scope": entity_scope_name(label, uri),
    }
    MCP_CONFIG.write_text(
        json.dumps(
            {
                "llm_created_mcp": {
                    "command": PYTHON,
                    "args": [
                        str(
                            REPO
                            / "ai_generated_contents_ontosyn_regen_v3/_launch_ontosynthesis_mcp.py"
                        )
                    ],
                    "transport": "stdio",
                    "cwd": str(REPO),
                    "env": {
                        "PYTHONPATH": str(REPO),
                        "PYTHONIOENCODING": "utf-8",
                        "TWA_GENERATED_ARTIFACT_ROOT": str(
                            REPO / "ai_generated_contents_ontosyn_regen_v3"
                        ),
                        "TWA_AGENTIC_DATA_DIR": str(RUNTIME),
                    },
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    old_hint = (
        SRC_PAPER
        / "mcp_run"
        / "iter2_hints_Zr-bpydc-CuCl2_post-synthetic_metallization_of_Zr-bpydc.txt"
    )
    names["old_iter2_hint_chars"] = old_hint.stat().st_size if old_hint.is_file() else None
    names["copied_iter1_files"] = copied
    names["kept_iter1"] = (PAPER / "iteration_1.ttl").is_file()
    return names


def extraction_config(extract_safe: str) -> dict:
    return {
        "project_root": str(REPO),
        "data_dir": str(RUNTIME),
        "meta_task_config": "configs/meta_task/meta_task_config.json",
        "ontology_name": "ontosynthesis",
        "only_extraction_iterations": [2, 3, 4],
        "only_entity_safe": extract_safe,
    }


def kg_config(kg_safe: str) -> dict:
    return {
        "project_root": str(REPO),
        "data_dir": str(RUNTIME),
        "meta_task_config": "configs/meta_task/meta_task_config.json",
        "ontology_name": "ontosynthesis",
        "test_mcp_config": str(MCP_CONFIG),
        "start_main_kg_iteration": 2,
        "only_entity_safe": kg_safe,
    }


def _hint_summary(paper: Path, extract_safe: str) -> dict:
    rows = {}
    for iteration in (2, 3, 4):
        path = paper / "mcp_run" / f"iter{iteration}_hints_{extract_safe}.txt"
        if not path.is_file():
            rows[f"iter{iteration}"] = None
            continue
        text = path.read_text(encoding="utf-8")
        rows[f"iter{iteration}"] = {
            "chars": len(text),
            "n_percent": text.count("%"),
            "has_dtxsid": "DTXSID" in text,
            "sample": text[:240].replace("\n", " "),
        }
    return rows


def main() -> None:
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--kg-only", action="store_true")
    parser.add_argument("--extract-only", action="store_true")
    parser.add_argument("--keep-runtime", action="store_true")
    args = parser.parse_args()

    os.chdir(REPO)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    os.environ["TWA_GENERATED_ARTIFACT_ROOT"] = str(
        REPO / "ai_generated_contents_ontosyn_regen_v3"
    )
    os.environ["TWA_AGENTIC_DATA_DIR"] = str(RUNTIME)
    from dotenv import load_dotenv

    load_dotenv(REPO / ".env", override=True)
    info = prepare(keep_runtime=args.keep_runtime)
    print(json.dumps({"phase": "prepared", **info}, ensure_ascii=False, indent=2), flush=True)

    extract_ok = None
    kg_ok = None
    if not args.kg_only:
        from src.pipelines.main_ontology_extractions.extract import run_step as run_extract

        print("=== extraction iter2/3/4 from iter1 ===", flush=True)
        extract_ok = run_extract("50307a45", extraction_config(info["extract_safe"]))
        hint_summary = _hint_summary(PAPER, info["extract_safe"])
        print(
            json.dumps(
                {"phase": "extraction", "ok": extract_ok, "hints": hint_summary},
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
    if not args.extract_only:
        from src.pipelines.main_kg_building.build import run_step as run_kg

        print("=== kg from iter1 checkpoint ===", flush=True)
        kg_ok = run_kg("50307a45", kg_config(info["kg_safe"]))
        published = sorted(
            path.name
            for path in (PAPER / "ontosynthesis_output").glob("*ccc07bbe8914*.ttl")
        )
        print(
            json.dumps(
                {"phase": "kg", "ok": kg_ok, "published": published},
                indent=2,
            ),
            flush=True,
        )
    print(
        json.dumps(
            {
                "phase": "done",
                "extract_ok": extract_ok,
                "kg_ok": kg_ok,
                "label": info["label"],
                "hints": _hint_summary(PAPER, info["extract_safe"]),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    if extract_ok is False or kg_ok is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
