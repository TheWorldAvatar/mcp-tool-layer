"""Mint a scenario run and execute the extraction runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from models.generated_layout import resolve_generated_package_root
from models.locations import repository_root, resolve_under_repository
from src.extraction_runtime.domain_binding import load_runtime_domain
from src.extraction_runtime.locked_mechanisms import ONE_SHOT_ENV
from src.extraction_runtime.runner import DEFAULT_WORKERS, run_pipeline
from src.extraction_runtime.slim_extract_defaults import (
    apply_slim_ontosynthesis_extract_defaults,
)
from src.kg_building.experiment_protocol import (
    PROTOCOL_NAMES,
    apply_to_pipeline_config,
    lock_revision_policy,
    resolve_protocol,
    resolve_scorer_repo,
)


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _write_run_config(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _load_run_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"run config must be a JSON object: {path}")
    return payload


def _default_input_dir(scenario_domain: str) -> str:
    return f"scenarios/{scenario_domain}/datasets/eval30"


def mint_run_config(
    *,
    domain_name: str,
    input_dir: str | None,
    data_dir: str | None,
    steps: list[str] | None,
    vision: bool | None,
    reuse_conversion_artifacts_from: str | None,
    tag: str,
    generated_root: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    domain = load_runtime_domain(domain_name)
    repo = repository_root()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"{stamp}_{tag}" if tag else stamp
    run_dir = repo / "scenarios" / domain.scenario_domain / "runs" / run_id
    runtime = Path(data_dir) if data_dir else run_dir / "runtime"
    if not runtime.is_absolute():
        runtime = resolve_under_repository(runtime) if data_dir else runtime
    resolved_input = input_dir or _default_input_dir(domain.scenario_domain)
    config: dict[str, Any] = {
        "mode": "per_doi",
        "domain": domain.domain_id,
        "ontology": domain.ontology_name,
        "domain_config": str(
            Path("configs") / "domains" / f"{domain.ontology_name}.json"
        ),
        "input_dir": resolved_input,
        "data_dir": str(runtime.relative_to(repo)) if runtime.is_relative_to(repo) else str(runtime),
        "steps": list(steps or domain.default_steps),
        "execution_profile": domain.execution_profile,
        "vision_pdf_conversion": domain.vision_required if vision is None else vision,
    }
    if generated_root is not None:
        config["generated_artifact_root"] = str(generated_root)
    if reuse_conversion_artifacts_from:
        config["reuse_conversion_artifacts_from"] = reuse_conversion_artifacts_from
    apply_slim_ontosynthesis_extract_defaults(
        config, steps_were_explicit=bool(steps)
    )
    lock_revision_policy(config)
    run_dir.mkdir(parents=True, exist_ok=True)
    resolved_path = run_dir / "pipeline.resolved.json"
    _write_run_config(resolved_path, config)
    print(f"[OK] Minted run config: {resolved_path}")
    return resolved_path, config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run PDF-to-KG extraction from a domain config and generated artifacts."
    )
    parser.add_argument("ontology", help="Domain name; loads configs/domains/<name>.json")
    parser.add_argument("--config", help="Existing pipeline.resolved.json; skip minting")
    parser.add_argument("--domain-config", help="Override configs/domains/<ontology>.json")
    parser.add_argument("--input-dir", help="Directory of {doi}.pdf files")
    parser.add_argument("--data-dir", help="Override minted scenario runtime path")
    parser.add_argument("--hash", action="append", dest="hashes", help="Process only this hash")
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Max papers to extract/KG in parallel (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument("--test", action="store_true", help="Launch generated MCP from mcp_capabilities")
    parser.add_argument("--resume", action="store_true", help="Keep the existing runtime")
    parser.add_argument("--vision", action="store_true", help="Force vision PDF conversion")
    parser.add_argument("--no-vision", action="store_true", help="Force non-vision PDF conversion")
    parser.add_argument(
        "--reuse-conversion-artifacts-from",
        help="Optional markdown seed. Official chemistry extract ignores this and converts from PDF.",
    )
    parser.add_argument("--tag", default="extract", help="Run-id suffix when minting")
    parser.add_argument(
        "--generated-root",
        help="Exact generation package root (a generated/runs/<id>/ directory).",
    )
    parser.add_argument(
        "--generation-run",
        help="Generation batch id, tag, path, or 'current' / 'latest'.",
    )
    parser.add_argument(
        "--steps",
        help="Comma-separated step list. Overrides the domain default pipeline.",
    )
    parser.add_argument(
        "--until",
        help="Stop after this step (inclusive). Uses the minted or domain step list.",
    )
    parser.add_argument(
        "--protocol",
        choices=PROTOCOL_NAMES,
        help=(
            "Locked 1:1 KG experiment. Pins no-contract Pipeline KG (env, seed 42, "
            "gpt-4o-2024-11-20, firewall, no paper body, steps through "
            "main_kg_building). full-prompt adds official ONEPASS + T-Box. "
            "Implies --test."
        ),
    )
    parser.add_argument(
        "--score",
        action="store_true",
        help="After a protocol run, convert and score into this run's merged/ and scores/.",
    )
    parser.add_argument(
        "--scorer-repo",
        help="Read-only scorer checkout. Defaults to the Reproduction repo if present.",
    )
    parser.add_argument(
        "--compare-one-shot",
        action="store_true",
        help=(
            "Comparison ablation: one extract attempt and no LLM judges. "
            "Does not change the default locked revision path."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_stdio()
    args = build_parser().parse_args(argv)
    domain = load_runtime_domain(args.ontology, domain_config=args.domain_config)
    vision_override = True if args.vision else False if args.no_vision else None
    try:
        generated_root = resolve_generated_package_root(
            explicit=str(args.generated_root or "").strip() or None,
            generation_run=str(args.generation_run or "").strip() or None,
        )
    except FileNotFoundError as exc:
        print(f"[FAIL] {exc}")
        return 1
    if args.config:
        config_path = resolve_under_repository(args.config)
        config = _load_run_config(config_path)
    else:
        requested_steps = [
            item.strip()
            for item in str(args.steps or "").split(",")
            if item.strip()
        ]
        config_path, config = mint_run_config(
            domain_name=args.ontology,
            input_dir=args.input_dir,
            data_dir=args.data_dir,
            steps=requested_steps or None,
            vision=vision_override,
            reuse_conversion_artifacts_from=args.reuse_conversion_artifacts_from,
            tag=args.tag,
            generated_root=generated_root,
        )
    if args.generated_root or args.generation_run or "generated_artifact_root" not in config:
        config["generated_artifact_root"] = str(generated_root)
    if args.input_dir:
        config["input_dir"] = args.input_dir
    if args.compare_one_shot:
        os.environ[ONE_SHOT_ENV] = "1"
        config["compare_one_shot"] = True
    if args.until:
        steps = list(config.get("steps") or domain.default_steps)
        if args.until not in steps:
            print(f"[FAIL] --until {args.until} is not in steps: {', '.join(steps)}")
            return 1
        config["steps"] = steps[: steps.index(args.until) + 1]
    if args.protocol:
        if args.until or args.steps:
            print(
                "[WARN] --protocol locks steps through main_kg_building; "
                "ignoring --until / --steps"
            )
        try:
            resolve_protocol(args.protocol)
        except ValueError as exc:
            print(f"[FAIL] {exc}")
            return 1
        apply_to_pipeline_config(config, args.protocol)
        if not domain.vision_required:
            vision_override = False
        args.test = True
        print(f"[OK] Locked 1:1 protocol {args.protocol} (Pipeline no-contract)")
    if args.protocol or args.until or args.compare_one_shot:
        if not args.config or args.protocol or args.compare_one_shot:
            _write_run_config(config_path, config)
    if args.workers < 1:
        print("[FAIL] --workers must be at least 1")
        return 1
    ok = run_pipeline(
        config=config,
        config_path=config_path,
        domain=domain,
        input_dir=args.input_dir or config.get("input_dir"),
        only_hashes=args.hashes,
        use_test_mcp=args.test,
        resume_existing_runtime=args.resume,
        vision_override=vision_override,
        max_workers=args.workers,
    )
    if ok and args.score:
        scorer = resolve_scorer_repo(args.scorer_repo)
        if scorer is None:
            print("[FAIL] --score needs --scorer-repo (Reproduction checkout not found)")
            return 1
        _score_pipeline_run(
            config=config,
            config_path=config_path,
            hashes=args.hashes,
            scorer_repo=scorer,
            max_workers=args.workers,
        )
    return 0 if ok else 1


def _score_pipeline_run(
    *,
    config: dict,
    config_path: Path,
    hashes: list[str] | None,
    scorer_repo: Path,
    max_workers: int = DEFAULT_WORKERS,
) -> None:
    import sys

    ontology = str(config.get("ontology") or "").strip().lower()
    if ontology == "medical":
        print(
            "[OK] Skipping chemistry score_four for medical. "
            "Score with scripts/medical_ttl_to_csv_sparql.py and "
            "scripts/medical_score_predicted_vs_gold.py."
        )
        return

    ox = Path(__file__).resolve().parents[1] / "kg_building" / "ontologx"
    if str(ox) not in sys.path:
        sys.path.insert(0, str(ox))
    from score_four import convert_runtime_many, score_four

    runtime = Path(config["data_dir"])
    if not runtime.is_absolute():
        runtime = repository_root() / runtime
    run_dir = Path(config_path).resolve().parent
    merged = run_dir / "merged"
    scores = run_dir / "scores"
    paper_hashes = list(hashes or [])
    if not paper_hashes:
        paper_hashes = sorted(
            path.name for path in runtime.iterdir() if path.is_dir() and len(path.name) == 8
        )
    convert_runtime_many(
        data_dir=runtime,
        output_dir=merged,
        paper_hashes=paper_hashes,
        converter_repo=scorer_repo,
        max_workers=max_workers,
    )
    score_four(
        pred_root=merged,
        out_root=scores,
        paper_hashes=paper_hashes,
        scorer_repo=scorer_repo,
        max_workers=max_workers,
    )

