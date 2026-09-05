#!/usr/bin/env python3
"""Seed one official extraction and run DeepSeek V4 Flash KG building.

Uses the dedicated DeepSeek LLM path (thinking off, tool-capable providers).
Default case is official 0827 ``f4f7330e`` (single entity, complete hints).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SEED = (
    REPO
    / "experiments/20260903_indep10_official_pack/data/pipeline"
    / "off30_0827_indep10_gpt4o_pipe/runtime"
)
DEFAULT_HASH = "f4f7330e"
DEFAULT_RUN_ID = "20260903_dsv4flash_kg1"
ARTIFACT_ROOT = REPO / "ai_generated_contents_occurrence_surface_20260902_indep10"
META = "configs/meta_task/meta_task_config_official_indep10_dsv4_flash.json"
KEEP_MEMORY = {
    "top.ttl",
    "document.ttl",
    "document.provenance.json",
}


def _load_hash_mapping(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if value}


def strip_kg_artifacts(paper: Path) -> None:
    (paper / ".main_kg_building_done").unlink(missing_ok=True)
    output = paper / "ontosynthesis_output"
    if output.is_dir():
        for path in output.iterdir():
            if path.name.lower() == "top.ttl":
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    memory = paper / "memory"
    if memory.is_dir():
        for path in memory.iterdir():
            if path.name in KEEP_MEMORY or path.name.endswith(".identity.json"):
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    for name in ("intermediate_ttl_files", "exports"):
        folder = paper / name
        if folder.is_dir():
            shutil.rmtree(folder)
    for folder_name in ("responses", "prompts"):
        folder = paper / folder_name
        if not folder.is_dir():
            continue
        for child in list(folder.iterdir()):
            if "kg_building" in child.name:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()


def write_pipeline(run_dir: Path, runtime: Path) -> Path:
    payload = {
        "mode": "per_doi",
        "description": (
            "DeepSeek V4 Flash KG-only probe on one official 0827 extraction. "
            "Thinking disabled via LLMCreator DeepSeek path."
        ),
        "meta_task_config": META,
        "input_dir": "scenarios/mops/datasets/eval30",
        "data_dir": str(runtime).replace("\\", "/"),
        "steps": ["main_kg_building"],
        "scenario": {
            "domain": "mops",
            "dataset": "eval30",
            "run_id": run_dir.name,
            "tag": "dsv4flash-kg1",
            "evaluation_dir": str((run_dir / "evaluation").as_posix()),
        },
        "kg_full_hints_onepass": True,
        "kg_semantic_surface_no_contract_experiment": True,
        "kg_generic_onepass_prompt_experiment": False,
        "kg_react_history_projection": True,
        "disable_kg_revisions": True,
        "kg_max_attempts": 1,
        "kg_hint_revision_max_attempts": 0,
        "post_publish_structural_retries": 0,
        "continuity_audit_retries": 0,
        "continuity_audit": {"enabled": False},
        "presence_coverage_audit": {"enabled": False},
        "disable_kg_posthoc_semantic_processing": True,
        "kg_argument_firewall_experiment": True,
    }
    path = run_dir / "pipeline.resolved.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def prepare(*, seed_from: Path, doi_hash: str, run_id: str, force: bool) -> dict:
    run_dir = REPO / "scenarios" / "mops" / "runs" / run_id
    runtime = run_dir / "runtime"
    paper = runtime / doi_hash
    source = seed_from / doi_hash
    if not source.is_dir():
        raise FileNotFoundError(f"seed paper missing: {source}")
    if paper.exists() and not force:
        raise FileExistsError(f"{paper} exists; pass --force to replace")
    run_dir.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    if paper.exists():
        shutil.rmtree(paper)
    shutil.copytree(source, paper)
    strip_kg_artifacts(paper)
    mapping = _load_hash_mapping(seed_from / "doi_to_hash.json")
    filtered = {doi: value for doi, value in mapping.items() if value == doi_hash}
    if not filtered:
        filtered = {doi_hash: doi_hash}
    (runtime / "doi_to_hash.json").write_text(
        json.dumps(filtered, indent=2) + "\n", encoding="utf-8"
    )
    config = write_pipeline(run_dir, runtime)
    return {
        "run_dir": str(run_dir),
        "runtime": str(runtime),
        "paper": str(paper),
        "config": str(config),
        "hash": doi_hash,
        "has_top_ttl": (paper / "ontosynthesis_output" / "top.ttl").is_file(),
        "has_extractions_done": (paper / ".main_ontology_extractions_done").is_file(),
        "kg_done_cleared": not (paper / ".main_kg_building_done").is_file(),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hash", default=DEFAULT_HASH)
    p.add_argument("--seed-from", type=Path, default=DEFAULT_SEED)
    p.add_argument("--run-id", default=DEFAULT_RUN_ID)
    p.add_argument("--force", action="store_true")
    p.add_argument("--prepare-only", action="store_true")
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--max-case-cost-usd", type=float, default=5.0)
    p.add_argument("--stall-seconds", type=float, default=1800)
    p.add_argument("--max-paper-seconds", type=float, default=3600)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    seed_from = args.seed_from if args.seed_from.is_absolute() else REPO / args.seed_from
    info = prepare(
        seed_from=seed_from,
        doi_hash=args.hash,
        run_id=args.run_id,
        force=args.force,
    )
    print(json.dumps(info, indent=2), flush=True)
    if args.prepare_only:
        return 0
    cmd = [
        args.python,
        str(REPO / "scripts" / "run_eval_queue.py"),
        "--config",
        info["config"],
        "--run-dir",
        info["run_dir"],
        "--hash",
        args.hash,
        "--until",
        "main_kg_building",
        "--no-extract-first",
        "--test",
        "--workers",
        "1",
        "--artifact-root",
        str(ARTIFACT_ROOT),
        "--max-case-cost-usd",
        str(args.max_case_cost_usd),
        "--stall-seconds",
        str(args.stall_seconds),
        "--max-paper-seconds",
        str(args.max_paper_seconds),
        "--python",
        args.python,
    ]
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(REPO)
    env["TWA_GENERATED_ARTIFACT_ROOT"] = str(ARTIFACT_ROOT)
    env["TWA_REQUIRE_GENERATED_ARTIFACT_ROOT"] = "1"
    env.setdefault("TWA_LLM_SEED", "42")
    return subprocess.call(cmd, cwd=str(REPO), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
